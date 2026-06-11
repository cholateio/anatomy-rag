"""pool 整合：經 PgBouncer :6432 真連線；同 SQL 跑兩次驗證 transaction pooling 相容。"""
import os

import pytest
from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool

pytestmark = pytest.mark.db


async def test_pool_roundtrip_via_pgbouncer(migrated_db):
    pool = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    try:
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT 1") == 1
        async with pool.acquire() as conn:
            # 同句再跑：statement_cache_size=0 下無 named prepared statement 殘留問題
            assert await conn.fetchval("SELECT 1") == 1
            assert await conn.fetchval("SELECT to_regclass('public.pages')") is not None
    finally:
        await pool.close()
