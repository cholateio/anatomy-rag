# ingest/tests/test_config.py
import pytest
from anatomy_ingest.config import IngestConfig


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pgbouncer:6432/anatomy_rag")
    monkeypatch.setenv("S3_BUCKET", "anatomy-rag-pages")
    monkeypatch.setenv("S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    cfg = IngestConfig.from_env()
    assert cfg.database_url.endswith("/anatomy_rag") and cfg.s3_bucket == "anatomy-rag-pages"


@pytest.mark.parametrize("url", [
    "postgresql://u:p@postgres:5432/anatomy_rag",   # 直連 Postgres
    "postgresql://u:p@host/anatomy_rag",            # 無 port
    "postgresql://u:p@host:6543/anatomy_rag",       # 其他 port（如 Supavisor）
])
def test_config_requires_port_6432(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    for k in ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.setenv(k, "x")
    with pytest.raises(ValueError, match="6432|PgBouncer"):
        IngestConfig.from_env()


def test_config_missing_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        IngestConfig.from_env()
