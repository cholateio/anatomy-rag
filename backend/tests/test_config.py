import pytest
from anatomy_backend.config import Settings
from pydantic import ValidationError

_BASE = {"PG_DIRECT_URL": "postgresql://u:p@postgres:5432/db", "REDIS_URL": "redis://redis:6379/0"}
_BASE_WITH_DB = {**_BASE, "DATABASE_URL": "postgresql://u:p@pgbouncer:6432/db"}


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


def test_database_url_without_explicit_port_rejected(monkeypatch):
    """無明確 port 的 DSN（urlparse().port 為 None）亦須被拒——最可能的誤設情形。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pgbouncer/db")  # 漏寫 :6432
    for k, v in _BASE.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match="6432"):
        Settings()


def test_missing_required_database_url_fails_fast(monkeypatch):
    """缺少必填的 DATABASE_URL 應於啟動即報錯（fail-fast）；以 _env_file=None 排除 .env 干擾。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for k, v in _BASE.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_pg_direct_url_must_target_postgres_5432(monkeypatch):
    """PG_DIRECT_URL 必須直連 Postgres :5432；指向 PgBouncer :6432 應被拒（解析 DSN port）。"""
    for k, v in _BASE_WITH_DB.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PG_DIRECT_URL", "postgresql://u:p@pgbouncer:6432/db")  # 錯指 PgBouncer
    with pytest.raises(ValidationError, match="5432"):
        Settings(_env_file=None)


def test_settings_has_appendix_a_fields(monkeypatch):
    """附錄 A 變數必須有對應欄位；extra=ignore 會靜默吞掉沒宣告的 key（審查遺留項）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pgbouncer:6432/db")
    monkeypatch.setenv("PG_DIRECT_URL", "postgresql://u:p@postgres:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("S3_BUCKET", "anatomy-rag-pages")
    monkeypatch.setenv("EVAL_OPENAI_API_KEY", "sk-eval-test")
    s = Settings(_env_file=None)
    assert s.s3_bucket == "anatomy-rag-pages"
    assert s.s3_endpoint == "http://minio:9000"
    assert s.eval_openai_api_key == "sk-eval-test"
    assert s.eval_openai_model == "gpt-5.5"
    assert s.langfuse_public_key == "" and s.langfuse_secret_key == ""
    assert s.sso_client_id == "" and s.sso_discovery_url == ""
    assert s.clinical_flavored_logging is False
