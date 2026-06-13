import os
import uuid

import asyncpg
import numpy as np
import pytest
from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.db.tx_helpers import hnsw_search_txn
from anatomy_backend.retrieval.engine_selfbuilt import SelfBuiltEngine
from anatomy_backend.retrieval.query_repr import QueryRepr

pytestmark = pytest.mark.db
KB = 6


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


async def test_selfbuilt_requires_binary_tokens(pool):
    eng = SelfBuiltEngine()
    qr = QueryRepr(pooled_f32=tuple([0.0] * 128), tokens_bin=(), translated_q=None, lang="en")
    async with hnsw_search_txn(pool, ef_search=100) as conn:
        with pytest.raises(ValueError, match="binary"):
            await eng.retrieve(conn, qr, None, kb_version=KB, top_k=100, top_n=10)


async def _seed_three(conn, page_ids, patches, pooled):
    await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
    await conn.execute(
        f"CREATE TABLE page_patches_v{KB} PARTITION OF page_patches FOR VALUES IN ({KB})")
    book = await conn.fetchval("INSERT INTO books (title) VALUES ('eng') RETURNING book_id")
    pv = "[" + ",".join(f"{x:.4f}" for x in pooled) + "]"
    for i, pid in enumerate(page_ids):
        await conn.execute(
            "INSERT INTO pages (page_id, book_id, page_num, page_image_uri, docling_md,"
            " metadata, pooled, kb_version, embed_model)"
            " VALUES ($1,$2,$3,'s3://x','md','{}'::jsonb,$4::halfvec,$5,'m')",
            pid, book, i + 1, pv, KB)
        recs = [(KB, pid, j, asyncpg.BitString.frombytes(p, bitlength=128))
                for j, p in enumerate(patches[pid])]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])


async def test_selfbuilt_sql_and_numpy_modes_agree(pool):
    rng = np.random.default_rng(21)
    page_ids = [uuid.uuid4() for _ in range(3)]
    patches = {pid: [rng.bytes(16) for _ in range(8)] for pid in page_ids}
    query_tokens = patches[page_ids[0]][:6]   # query=第1頁子集 → 第1頁 MaxSim 最高
    pooled = [float(x) for x in rng.standard_normal(128)]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_three(conn, page_ids, patches, pooled)
        qr = QueryRepr(pooled_f32=tuple(pooled), tokens_bin=tuple(query_tokens),
                       translated_q=None, lang="en")
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            sql = await SelfBuiltEngine("sql").retrieve(
                conn, qr, None, kb_version=KB, top_k=100, top_n=10)
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            npy = await SelfBuiltEngine("numpy").retrieve(
                conn, qr, None, kb_version=KB, top_k=100, top_n=10)
        assert sql.degraded is False and npy.degraded is False
        assert [p for p, _ in sql.ranked] == [p for p, _ in npy.ranked]
        assert sql.ranked[0][0] == page_ids[0]
        assert page_ids[0] in sql.coarse_ids   # Stage A 候選保留
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_selfbuilt_degraded_falls_back_to_stage_a(pool, monkeypatch):
    """Codex review #2：Stage B 逾時/錯誤 → savepoint 回滾、外層 txn 存活、
    回 degraded + Stage A 候選（§1.8）。以 monkeypatch 確定性模擬逾時。"""
    import anatomy_backend.retrieval.engine_selfbuilt as esb
    rng = np.random.default_rng(22)
    page_ids = [uuid.uuid4() for _ in range(3)]
    patches = {pid: [rng.bytes(16) for _ in range(8)] for pid in page_ids}
    pooled = [float(x) for x in rng.standard_normal(128)]

    async def _boom(conn, cand, tokens, kb, top_n):
        raise asyncpg.exceptions.QueryCanceledError("simulated statement timeout")

    monkeypatch.setitem(esb._STAGE_B, "sql", _boom)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_three(conn, page_ids, patches, pooled)
        qr = QueryRepr(pooled_f32=tuple(pooled),
                       tokens_bin=tuple(patches[page_ids[0]][:6]),
                       translated_q=None, lang="en")
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            er = await SelfBuiltEngine("sql").retrieve(
                conn, qr, None, kb_version=KB, top_k=100, top_n=10)
            alive = await conn.fetchval("SELECT 1")   # 外層 txn 未被連鎖 abort
        assert er.degraded is True
        assert er.ranked == []
        assert set(er.coarse_ids) == set(page_ids)    # Stage A 候選保留供降級
        assert alive == 1
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
