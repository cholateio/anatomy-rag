# ingest/tests/conftest.py
"""ingest 測試 fixtures：db 守門（沿用 backend conftest 慣例）+ 真連線。

db 標記測試需 DATABASE_URL（:6432）+ PG_DIRECT_URL（:5432，建 schema 用）。
destructive 守門：DB 名須以 _test 結尾或設 ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1。
"""
import os
from urllib.parse import urlparse

import pytest

_DB_ENV_READY = bool(os.environ.get("DATABASE_URL")) and bool(os.environ.get("PG_DIRECT_URL"))


def pytest_configure(config):
    if os.environ.get("REQUIRE_DB_TESTS") == "1" and not _DB_ENV_READY:
        raise pytest.UsageError("REQUIRE_DB_TESTS=1 但缺 DATABASE_URL / PG_DIRECT_URL")
    if _DB_ENV_READY:
        db_name = urlparse(os.environ["DATABASE_URL"]).path.lstrip("/")
        if not db_name.endswith("_test") and os.environ.get(
            "ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE"
        ) != "1":
            raise pytest.UsageError(
                "db 測試會寫入/清空目標資料庫；DB 名須以 _test 結尾或設 "
                "ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1"
            )


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(
        reason="需要 DATABASE_URL + PG_DIRECT_URL（CI db-integration 或本機 compose）"
    )
    for item in items:
        if "db" in item.keywords and not _DB_ENV_READY:
            item.add_marker(skip_db)
