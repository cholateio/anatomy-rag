"""D-G：SET LOCAL hnsw.ef_search 必須與 HNSW SELECT 同一 transaction
（transaction pooling 下同 txn = 同一後端連線，設定才作用到該查詢）。"""
import os

import pytest
from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.db.tx_helpers import hnsw_search_txn

pytestmark = pytest.mark.db


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


async def test_set_local_scoped_to_txn(pool):
    async with pool.acquire() as conn:
        baseline = await conn.fetchval("SELECT current_setting('hnsw.ef_search')")
    async with hnsw_search_txn(pool, ef_search=100) as conn:
        assert await conn.fetchval("SELECT current_setting('hnsw.ef_search')") == "100"
        assert (
            await conn.fetchval("SELECT current_setting('hnsw.iterative_scan')")
            == "strict_order"
        )
    async with pool.acquire() as conn:
        # txn 結束後恢復 baseline（SET LOCAL 不外洩——D-G 的隔離；不綁死 pgvector 預設值）
        assert await conn.fetchval("SELECT current_setting('hnsw.ef_search')") == baseline


@pytest.mark.parametrize("bad", ["100", 0, -5, True, 1001])
async def test_ef_search_validation(pool, bad):
    with pytest.raises(ValueError):
        async with hnsw_search_txn(pool, ef_search=bad):
            pass


async def test_stage_a_query_fills_topk_across_versions(pool):
    """HIGH-1 回歸：HNSW 是跨版本全域索引，blue-green 雙版本並存時
    active 版本仍須回滿 Top-K（iterative_scan=strict_order 生效證明）。"""
    import json

    import numpy as np

    rng = np.random.default_rng(7)

    def vec() -> str:
        return "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"

    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
        book = await conn.fetchval(
            "INSERT INTO books (title) VALUES ('topk-fill') RETURNING book_id"
        )
        rows = [
            (book, n, "s3://x.png", f"page {n}", json.dumps({}), vec(), kb, "colpali-v1.3-hf")
            for kb in (1, 2)
            for n in range(120)
        ]
        await conn.executemany(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1, $2, $3, $4, $5::jsonb, $6::halfvec, $7, $8)",
            rows,
        )
    async with hnsw_search_txn(pool, ef_search=100) as conn:
        # 小資料集 planner 會偏好 seq scan（那就測不到索引行為）；強制走 HNSW
        await conn.execute("SET LOCAL enable_seqscan = off")
        hits = await conn.fetch(
            "SELECT page_id FROM pages WHERE kb_version = 1"
            " ORDER BY pooled <=> $1::halfvec LIMIT 100",
            vec(),
        )
    assert len(hits) == 100  # 非 iterative 模式下雙版本約只回 ~50 筆
