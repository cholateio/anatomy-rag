import json
import uuid

import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.db.tx_helpers import hnsw_search_txn
from anatomy_backend.retrieval.stage_a import stage_a_coarse

pytestmark = pytest.mark.db
import os


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


def _vec(rng) -> str:
    return "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"


async def _seed_pages(conn, n, kb, *, metadata=None, book_title="stage-a"):
    book = await conn.fetchval(
        "INSERT INTO books (title) VALUES ($1) RETURNING book_id", book_title)
    rng = np.random.default_rng(42)
    rows = [
        (book, i + 1, "s3://x.png", f"page {i}", json.dumps(metadata or {}),
         _vec(rng), kb, "colpali-v1.3-hf")
        for i in range(n)
    ]
    await conn.executemany(
        "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
        " pooled, kb_version, embed_model) VALUES ($1,$2,$3,$4,$5::jsonb,$6::halfvec,$7,$8)",
        rows)
    return book


async def test_stage_a_returns_top_k(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_pages(conn, 120, kb=1)
        q = [float(x) for x in np.random.default_rng(7).standard_normal(128)]
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL enable_sort = off")
            res = await stage_a_coarse(conn, q, None, kb_version=1, top_k=100)
        assert len(res) == 100
        assert all(isinstance(p, uuid.UUID) for p in res)
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_a_filters_kb_version(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_pages(conn, 100, kb=1, book_title="v1")
            await _seed_pages(conn, 100, kb=2, book_title="v2")
        q = [float(x) for x in np.random.default_rng(7).standard_normal(128)]
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            res = await stage_a_coarse(conn, q, None, kb_version=1, top_k=100)
            # 全部回傳的 page 都屬 kb_version=1
            kbs = await conn.fetch(
                "SELECT DISTINCT kb_version FROM pages WHERE page_id = ANY($1::uuid[])", res)
        assert {r["kb_version"] for r in kbs} == {1}
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_a_metadata_filter(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_pages(conn, 40, kb=1, metadata={"anatomy_system": "nervous"},
                              book_title="nerve")
            await _seed_pages(conn, 40, kb=1, metadata={"anatomy_system": "muscular"},
                              book_title="muscle")
        q = [float(x) for x in np.random.default_rng(7).standard_normal(128)]
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            res = await stage_a_coarse(
                conn, q, {"anatomy_system": "nervous"}, kb_version=1, top_k=100)
            systems = await conn.fetch(
                "SELECT metadata->>'anatomy_system' AS s FROM pages"
                " WHERE page_id = ANY($1::uuid[])", res)
        assert {r["s"] for r in systems} == {"nervous"}
        assert len(res) == 40
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_a_returns_true_nearest_against_oracle(pool):
    """植入式最近鄰（Codex review #3）：300 頁 > Top-K、用真實 planner（不強制設定），
    抓 halfvec 序列化錯 / 距離方向反 / 排名錯——前述測試僅斷言『回 100 筆』不足以驗正確性。"""
    rng = np.random.default_rng(101)
    q = rng.standard_normal(128).astype(np.float32)
    qn = q / np.linalg.norm(q)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            book = await conn.fetchval(
                "INSERT INTO books (title) VALUES ('oracle') RETURNING book_id")
            rows = []
            for i in range(5):  # 植入頁：方向≈query（query+微噪）→ cosine 必最近
                v = qn + 0.01 * rng.standard_normal(128).astype(np.float32)
                rows.append((i + 1, "[" + ",".join(f"{x:.6f}" for x in v) + "]"))
            for i in range(295):  # 遠頁：隨機方向（高維下與 query 近正交）
                v = rng.standard_normal(128).astype(np.float32)
                rows.append((i + 6, "[" + ",".join(f"{x:.6f}" for x in v) + "]"))
            ids = []
            for num, vec in rows:
                pid = await conn.fetchval(
                    "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md,"
                    " metadata, pooled, kb_version, embed_model)"
                    " VALUES ($1,$2,'s3://x','md','{}'::jsonb,$3::halfvec,1,'m')"
                    " RETURNING page_id", book, num, vec)
                ids.append(pid)
            planted = set(ids[:5])
        # 不強制 planner 設定 → 真實索引路徑（Codex review #3：不被 forced settings 遮蔽）
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            res = await stage_a_coarse(
                conn, [float(x) for x in qn], None, kb_version=1, top_k=20)
        assert planted.issubset(set(res[:20]))   # 5 植入頁全進 top-20
        assert res[0] in planted                 # top-1 為植入頁（距離方向正確）
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
