"""backend 測試共用 fixtures。

db 標記測試需要 DATABASE_URL（:6432 經 PgBouncer）+ PG_DIRECT_URL（:5432，僅 alembic）。
兩者未設定時自動 skip——unit job 與裸 `make test`（無 compose）不受影響；
CI db-integration job 與本機 compose 環境會真跑。
"""
import os
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

_DB_ENV_READY = bool(os.environ.get("DATABASE_URL")) and bool(os.environ.get("PG_DIRECT_URL"))


def pytest_configure(config):
    # CI db-integration 設 REQUIRE_DB_TESTS=1：env 漏傳時直接 fail，
    # 不允許 db 測試整批 skip 還回綠燈（假綠防呆，Codex 審查 MEDIUM）
    if os.environ.get("REQUIRE_DB_TESTS") == "1" and not _DB_ENV_READY:
        raise pytest.UsageError("REQUIRE_DB_TESTS=1 但缺 DATABASE_URL / PG_DIRECT_URL")


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(
        reason="需要 DATABASE_URL + PG_DIRECT_URL（CI db-integration 或本機 compose）"
    )
    for item in items:
        if "db" in item.keywords and not _DB_ENV_READY:
            item.add_marker(skip_db)


@pytest.fixture(scope="session")
def alembic_cfg():
    """alembic Config，script_location 設為絕對路徑——不依賴 cwd（CI 在 repo 根跑 pytest）。"""
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(BACKEND_DIR / "src" / "anatomy_backend" / "db" / "migrations")
    )
    return cfg


@pytest.fixture(scope="session")
def migrated_db(alembic_cfg):
    """整個測試 session 先 upgrade 到 head（冪等；CI 的獨立 alembic step 已跑過也無妨）。"""
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    yield


@pytest.fixture
async def db_conn(migrated_db):
    """單一 asyncpg 連線（經 PgBouncer :6432；transaction pooling 必須 statement_cache_size=0）。"""
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def clean_db(db_conn):
    """每測試前清空資料（TRUNCATE books CASCADE 沿 FK 連到 pages 與 page_patches 各分區）。
    Task 6 追加 query_logs / ingest_errors 後，直接加進 TRUNCATE 清單即可。
    """
    await db_conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
    return db_conn
