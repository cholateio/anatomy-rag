import os
import uuid

import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.stage_b import stage_b_maxsim
from anatomy_eval.reference import maxsim_hamming

pytestmark = pytest.mark.db
KB = 5


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


async def _seed_patches(conn, pages_patches: dict, kb):
    """pages_patches: {page_id: [bytes16, ...]}。建分區 + pages + page_patches。"""
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb})")
    book = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('stage-b') RETURNING book_id")
    pooled = "[" + ",".join("0.01" for _ in range(128)) + "]"
    for i, (pid, patches) in enumerate(pages_patches.items()):
        await conn.execute(
            "INSERT INTO pages (page_id, book_id, page_num, page_image_uri, docling_md,"
            " metadata, pooled, kb_version, embed_model)"
            " VALUES ($1,$2,$3,'s3://x','md','{}'::jsonb,$4::halfvec,$5,'m')",
            pid, book, i + 1, pooled, kb)
        recs = [(kb, pid, j, asyncpg_bits(p)) for j, p in enumerate(patches)]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
    return book


def asyncpg_bits(b: bytes):
    import asyncpg
    return asyncpg.BitString.frombytes(b, bitlength=128)


async def test_stage_b_matches_oracle(pool):
    rng = np.random.default_rng(3)
    pages = {uuid.uuid4(): [rng.bytes(16) for _ in range(8)] for _ in range(5)}
    query = [rng.bytes(16) for _ in range(6)]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_patches(conn, pages, KB)
            cand = list(pages.keys())
            res = await stage_b_maxsim(conn, cand, query, kb_version=KB, top_n=10)
        # 與 oracle 手算逐頁比對
        oracle = {pid: maxsim_hamming(query, patches) for pid, patches in pages.items()}
        oracle_ranked = sorted(oracle, key=lambda p: -oracle[p])
        assert [pid for pid, _ in res] == oracle_ranked
        for pid, score in res:
            assert abs(score - oracle[pid]) < 1e-6
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_b_only_scans_candidates(pool):
    rng = np.random.default_rng(9)
    pages = {uuid.uuid4(): [rng.bytes(16) for _ in range(8)] for _ in range(6)}
    query = [rng.bytes(16) for _ in range(6)]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_patches(conn, pages, KB)
            all_ids = list(pages.keys())
            cand = all_ids[:5]                      # 第 6 頁不在候選
            res = await stage_b_maxsim(conn, cand, query, kb_version=KB, top_n=10)
        returned = {pid for pid, _ in res}
        assert all_ids[5] not in returned
        assert returned.issubset(set(cand))
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


# 邊界位元向量（MSB-first）：抓 to_pg_bits 入庫 ↔ SQL bit ops ↔ oracle 的位序一致性
B0 = b"\x80" + b"\x00" * 15        # 只設 bit 0（byte0 MSB）
B127 = b"\x00" * 15 + b"\x01"      # 只設 bit 127（byte15 LSB）
B56 = b"\x00" * 7 + b"\x80" + b"\x00" * 8   # 只設 bit 56


async def _seed_via_to_pg_bits(conn, pages: dict, kb):
    """走『生產入庫路徑』灌 patch（to_pg_bits → text → ::bit(128)，同 ingest/writer.py），
    而非 BitString.frombytes——證明真實儲存表示與讀取/oracle 三方一致（Codex review #4）。"""
    from anatomy_shared.binary import to_pg_bits
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb})")
    book = await conn.fetchval("INSERT INTO books (title) VALUES ('edge') RETURNING book_id")
    pv = "[" + ",".join("0.01" for _ in range(128)) + "]"
    for i, (pid, patches) in enumerate(pages.items()):
        await conn.execute(
            "INSERT INTO pages (page_id, book_id, page_num, page_image_uri, docling_md,"
            " metadata, pooled, kb_version, embed_model)"
            " VALUES ($1,$2,$3,'s3://x','md','{}'::jsonb,$4::halfvec,$5,'m')",
            pid, book, i + 1, pv, kb)
        for j, pb in enumerate(patches):
            await conn.execute(
                "INSERT INTO page_patches (kb_version, page_id, patch_idx, patch_bin)"
                " VALUES ($1,$2,$3,$4::text::bit(128))", kb, pid, j, to_pg_bits(pb))
    return book


async def test_stage_b_edge_bits_via_real_storage(pool):
    pages = {uuid.uuid4(): [B0, B127], uuid.uuid4(): [B127, B56], uuid.uuid4(): [B56, B0]}
    query = [B0, B127]   # 非對稱：與不同 patch 的 hamming 差異隨位序而變
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_via_to_pg_bits(conn, pages, KB)
            cand = list(pages.keys())
            res = await stage_b_maxsim(conn, cand, query, kb_version=KB, top_n=10)
        oracle = {pid: maxsim_hamming(query, patches) for pid, patches in pages.items()}
        for pid, score in res:
            assert abs(score - oracle[pid]) < 1e-6, f"位序不一致 @ {pid}"
        assert [p for p, _ in res] == sorted(oracle, key=lambda p: -oracle[p])
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
