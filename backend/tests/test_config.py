import pytest
from anatomy_backend.config import Settings

_BASE = {"PG_DIRECT_URL": "postgresql://u:p@postgres:5432/db", "REDIS_URL": "redis://redis:6379/0"}


def test_defaults_dev_mode(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pgbouncer:6432/db")
    for k, v in _BASE.items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.encoder_mock and s.llm_mock and s.auth_mode == "dev" and s.active_kb_version == 1


def test_database_url_must_target_pgbouncer_6432(monkeypatch):
    """應用層 DATABASE_URL 必須走 PgBouncer :6432（解析 DSN port，非字串搜尋）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres:5432/db")  # 直連 Postgres
    for k, v in _BASE.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match="6432"):
        Settings()
