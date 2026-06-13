import os
import uuid

import asyncpg
import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.orchestrator import retrieve
from anatomy_backend.retrieval.query_repr import QueryRepr
from anatomy_backend.retrieval.types import RetrievalResult

pytestmark = pytest.mark.db
KB = 7


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


async def _seed(conn, kb, pages):
    """pages: list[(page_num, docling_md, patches:list[bytes16])]，第 0 頁為 query 目標頁。"""
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb})")
    book = await conn.fetchval(
        "INSERT INTO books (title, edition) VALUES ('Atlas','42e') RETURNING book_id")
    rng = np.random.default_rng(5)
    ids = []
    for num, md, patches in pages:
        pv = "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1,$2,'s3://p.png',$3,'{\"figures\":[\"1.1\"]}'::jsonb,$4::halfvec,$5,'m')"
            " RETURNING page_id", book, num, md, pv, kb)
        ids.append(pid)
        recs = [(kb, pid, j, asyncpg.BitString.frombytes(p, bitlength=128))
                for j, p in enumerate(patches)]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
    return book, ids


async def test_retrieve_returns_ranked_results(pool):
    rng = np.random.default_rng(13)
    target_patches = [rng.bytes(16) for _ in range(8)]
    pages = [
        (1, "biceps brachii origin insertion", target_patches),
        (2, "femur thigh bone", [rng.bytes(16) for _ in range(8)]),
        (3, "scapula shoulder", [rng.bytes(16) for _ in range(8)]),
    ]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            _, ids = await _seed(conn, KB, pages)
        # query：tokens = target page patches（MaxSim 最高）；translated_q 命中第 1 頁文字
        qr = QueryRepr(
            pooled_f32=tuple(float(x) for x in rng.standard_normal(128)),
            tokens_bin=tuple(target_patches[:6]),
            translated_q="biceps brachii origin", lang="zh")
        res = await retrieve(pool, "二頭肌起止點", qr, None, kb_version=KB, top_n=3)
        assert isinstance(res, list) and all(isinstance(r, RetrievalResult) for r in res)
        assert res[0].page_id == ids[0]           # 視覺 + 文字雙命中 → RRF 最高
        assert res[0].book_title == "Atlas" and res[0].edition == "42e"
        assert res[0].metadata["figures"] == ["1.1"]
        # 順序 = RRF 遞減（score 單調不增）
        assert [r.score for r in res] == sorted((r.score for r in res), reverse=True)
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_retrieve_bm25_uses_translated_q(pool):
    rng = np.random.default_rng(17)
    pages = [
        (1, "clavicle collarbone", [rng.bytes(16) for _ in range(8)]),
        (2, "patella kneecap", [rng.bytes(16) for _ in range(8)]),
    ]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            _, ids = await _seed(conn, KB, pages)
        # 原文中文不會命中英文 tsvector；translated_q='clavicle' 命中第 1 頁
        qr = QueryRepr(pooled_f32=tuple([0.0] * 128),
                       tokens_bin=tuple(rng.bytes(16) for _ in range(6)),
                       translated_q="clavicle", lang="zh")
        res = await retrieve(pool, "鎖骨", qr, None, kb_version=KB, top_n=3)
        # 第 1 頁因 BM25（translated_q）入榜
        assert ids[0] in [r.page_id for r in res]
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_retrieve_degrades_on_stage_b_failure(pool, monkeypatch):
    """Codex review #2：Stage B 失敗時 orchestrator 仍回結果（Stage A 降級 + BM25），
    非整請求失敗——savepoint 隔離使外層 txn 的 BM25/metadata 照常。"""
    import anatomy_backend.retrieval.engine_selfbuilt as esb
    rng = np.random.default_rng(19)
    target_patches = [rng.bytes(16) for _ in range(8)]
    pages = [
        (1, "biceps brachii origin", target_patches),
        (2, "femur thigh bone", [rng.bytes(16) for _ in range(8)]),
    ]

    async def _boom(conn, cand, tokens, kb, top_n):
        raise asyncpg.exceptions.QueryCanceledError("simulated statement timeout")

    monkeypatch.setitem(esb._STAGE_B, "sql", _boom)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            _, ids = await _seed(conn, KB, pages)
        qr = QueryRepr(
            pooled_f32=tuple(float(x) for x in rng.standard_normal(128)),
            tokens_bin=tuple(target_patches[:6]),
            translated_q="biceps brachii origin", lang="zh")
        res = await retrieve(pool, "二頭肌", qr, None, kb_version=KB, top_n=3)
        assert len(res) >= 1                          # 降級仍回非空（Stage A + BM25 RRF）
        assert ids[0] in [r.page_id for r in res]
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
