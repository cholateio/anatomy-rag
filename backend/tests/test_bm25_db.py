import os

import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.bm25 import bm25_search

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


async def _seed(conn, kb, pages: list[tuple[int, str]]):
    book = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('bm25') RETURNING book_id")
    pooled = "[" + ",".join("0.01" for _ in range(128)) + "]"
    ids = {}
    for num, md in pages:
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1,$2,'s3://x',$3,'{}'::jsonb,$4::halfvec,$5,'m') RETURNING page_id",
            book, num, md, pooled, kb)
        ids[num] = pid
    return ids


async def test_bm25_ranks_matching_page(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            ids = await _seed(conn, 1, [
                (1, "The biceps brachii origin and insertion on the radius"),
                (2, "The femur is the thigh bone of the lower limb"),
            ])
            res = await bm25_search(conn, "biceps brachii origin", kb_version=1, top_k=50)
        assert res[0] == ids[1]
        assert ids[2] not in res  # 無 term 命中 → 不在 @@ 結果
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_bm25_respects_kb_version(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed(conn, 1, [(1, "biceps brachii origin")])
            ids2 = await _seed(conn, 2, [(1, "biceps brachii origin")])
            res = await bm25_search(conn, "biceps", kb_version=2, top_k=50)
        assert res == [ids2[1]]
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
