"""kb_version helper 參數驗證（無 DB；表名拼接前必須擋非 int——SQL injection 防線）。"""
import pytest
from anatomy_backend.db.kb_version import ensure_kb_partition, get_active_kb_version


class _FakeConn:
    def __init__(self):
        self.sql = None

    async def execute(self, sql):
        self.sql = sql


async def test_ensure_kb_partition_builds_expected_ddl():
    conn = _FakeConn()
    await ensure_kb_partition(conn, 7)
    assert conn.sql == (
        "CREATE TABLE IF NOT EXISTS page_patches_v7 "
        "PARTITION OF page_patches FOR VALUES IN (7)"
    )


@pytest.mark.parametrize("bad", ["7", 7.0, True, 0, -1, None])
async def test_ensure_kb_partition_rejects_non_positive_int(bad):
    with pytest.raises(ValueError):
        await ensure_kb_partition(_FakeConn(), bad)


def test_get_active_kb_version_reads_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:6432/db")
    monkeypatch.setenv("PG_DIRECT_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("ACTIVE_KB_VERSION", "4")
    from anatomy_backend.config import Settings

    assert get_active_kb_version(Settings(_env_file=None)) == 4
