"""Alembic 可逆性（§3.5）：upgrade head ↔ downgrade base 無殘留。

同步測試（非 async）：env.py 內部 asyncio.run() 不能在已有 event loop 的協程內呼叫。
"""
import asyncio
import os

import pytest
from alembic import command

pytestmark = pytest.mark.db

# Task 5/6 會把 page_patches / query_logs / ingest_errors 追加進來（隨 migration 鏈成長）
TABLES = ["books", "pages"]


def _fetchval(sql: str):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(os.environ["PG_DIRECT_URL"], statement_cache_size=0)
        try:
            return await conn.fetchval(sql)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_upgrade_downgrade_roundtrip(alembic_cfg):
    try:
        command.upgrade(alembic_cfg, "head")
        for t in TABLES:
            assert _fetchval(f"SELECT to_regclass('public.{t}')") is not None, f"{t} 應存在"

        command.downgrade(alembic_cfg, "base")
        for t in TABLES:
            assert _fetchval(f"SELECT to_regclass('public.{t}')") is None, f"{t} 應已移除"
        leftover = _fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
            "AND tablename NOT IN ('alembic_version')"
        )
        assert leftover == 0, "downgrade base 後不得殘留任何表"
    finally:
        # 不論成敗都回 head：中途 assert 失敗不可把 base 狀態留給同 session 的其他 db 測試
        command.upgrade(alembic_cfg, "head")
    assert _fetchval("SELECT to_regclass('public.pages')") is not None


def test_pgvector_version_at_least_0_8(migrated_db):
    """spec §3.1 MUST pgvector ≥0.8（halfvec/HNSW iterative_scan 皆依賴；映像可變 tag 需實測）。"""
    ver = _fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    major, minor = (int(x) for x in ver.split(".")[:2])
    assert (major, minor) >= (0, 8), f"pgvector {ver} < 0.8"
