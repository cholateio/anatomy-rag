"""Alembic 可逆性（§3.5）：upgrade head ↔ downgrade base 無殘留。

同步測試（非 async）：env.py 內部 asyncio.run() 不能在已有 event loop 的協程內呼叫。
"""
import asyncio
import os

import pytest
from alembic import command

pytestmark = pytest.mark.db

TABLES = ["books", "pages", "page_patches", "query_logs", "ingest_errors"]


def _fetchval(sql: str):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
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


def test_008_turn_id_schema(alembic_cfg, migrated_db):
    """008 migration：turn_id 欄位型別、UNIQUE 索引、多 NULL 共存、重複非 NULL 拒絕。"""
    import asyncpg

    async def _go():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
        try:
            # 確保 head 已套用
            col_type = await conn.fetchval(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='query_logs' AND column_name='turn_id'"
            )
            assert col_type == "uuid", f"turn_id 型別應為 uuid，得 {col_type!r}"

            # UNIQUE 索引存在
            idx = await conn.fetchval(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='query_logs' "
                "AND indexname='uq_query_logs_turn_id'"
            )
            assert idx == "uq_query_logs_turn_id", "缺少 uq_query_logs_turn_id 索引"

            # 兩列 NULL turn_id 可共存（nullable UNIQUE）
            await conn.execute(
                "INSERT INTO query_logs (user_id, query_text) VALUES "
                "('00000000-0000-0000-0000-000000000001', 'q1'), "
                "('00000000-0000-0000-0000-000000000002', 'q2')"
            )

            # 插入非 NULL turn_id
            tid = "00000000-0000-0000-0000-0000000000bb"
            await conn.execute(
                "INSERT INTO query_logs (user_id, query_text, turn_id) "
                "VALUES ('00000000-0000-0000-0000-000000000003', 'q3', $1::uuid)",
                tid,
            )

            # 重複非 NULL turn_id → UniqueViolationError
            try:
                await conn.execute(
                    "INSERT INTO query_logs (user_id, query_text, turn_id) "
                    "VALUES ('00000000-0000-0000-0000-000000000004', 'q4', $1::uuid)",
                    tid,
                )
                raise AssertionError("重複 turn_id 應拋 UniqueViolationError")
            except asyncpg.UniqueViolationError:
                pass  # 預期

            # 清理（downgrade 前）
            await conn.execute("TRUNCATE query_logs RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_go())

    # downgrade -1：欄位與索引應消失
    command.downgrade(alembic_cfg, "007_ingest_errors")
    try:
        col_after = _fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='query_logs' AND column_name='turn_id'"
        )
        assert col_after is None, "downgrade 後 turn_id 欄位應已移除"
        idx_after = _fetchval(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='query_logs' "
            "AND indexname='uq_query_logs_turn_id'"
        )
        assert idx_after is None, "downgrade 後 uq_query_logs_turn_id 索引應已移除"
    finally:
        # 還原 head
        command.upgrade(alembic_cfg, "head")
