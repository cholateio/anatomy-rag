"""Alembic 環境設定 — migrations 專用直連 Postgres :5432（§0.3 例外）。

架構規則 §0.3：
- 後端所有程式碼 MUST 透過 PgBouncer :6432（DATABASE_URL）連線。
- Alembic migrations 是唯一允許直連 Postgres :5432 的例外，
  因為 transaction pooling 模式不支援 DDL 所需的 session-level 操作。
- 本模組從環境變數 PG_DIRECT_URL 取得 migrations 專用連線字串，
  禁止 hardcode 任何 URL 或密碼。
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Alembic Config 物件，提供對 alembic.ini 的存取
config = context.config

# 設定 Python logging（讀取 alembic.ini 的 [loggers] 區段）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Phase 0 尚無 ORM 模型；migrations 採純 SQL（raw DDL）編寫。
# Phase 2 起引入 SQLAlchemy ORM 後，改為對應的 Base.metadata。
target_metadata = None


def _get_pg_direct_url() -> str:
    """從環境變數取得 migrations 專用直連 URL。

    Returns:
        PG_DIRECT_URL 的值（指向 Postgres :5432）。

    Raises:
        RuntimeError: 若 PG_DIRECT_URL 未設定。
    """
    url = os.environ.get("PG_DIRECT_URL")
    if not url:
        raise RuntimeError(
            "Alembic 需要 PG_DIRECT_URL"
            "（直連 Postgres :5432 的 migrations 專用連線，§0.3 例外）"
        )
    # 本專案單一 postgres 驅動 = asyncpg（不引入 psycopg2）；SQLAlchemy 需明確指定方言。
    # .env 的 PG_DIRECT_URL 維持泛用 postgresql://，此處正規化為 +asyncpg。
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def run_migrations_offline() -> None:
    """離線模式：產生 SQL 腳本輸出，不建立實際 DB 連線。

    適用於 --sql 旗標（產生可供人工審閱的 DDL 腳本）。
    """
    url = _get_pg_direct_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """在已建立的（同步化）連線上執行 migrations。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """線上模式：以 asyncpg 直連 Postgres :5432 執行 DDL（§0.3 例外）。

    使用 NullPool 避免連線池在短暫 migration run 後殘留連線；
    透過 run_sync 在 async 連線上跑同步的 Alembic migration 流程。
    """
    url = _get_pg_direct_url()
    connectable = create_async_engine(url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
