"""設定層；以 DSN 解析強制工程紅線：應用層連 PgBouncer :6432（§0.3）。"""
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 開發模式旗標：預設啟用 mock，CI / 本機皆可免 GPU / OpenAI key
    encoder_mock: bool = True
    llm_mock: bool = True
    auth_mode: str = "dev"
    dev_user_id: str = "00000000-0000-0000-0000-000000000001"

    # 資料庫連線（應用層必須走 PgBouncer :6432；migrations 用 PG_DIRECT_URL 直連）
    database_url: str
    pg_direct_url: str
    redis_url: str

    # Redis socket 逾時（秒）：Redis 接受連線後 stall 時，避免 cache/ratelimit 的 await 無限卡死。
    # fail-open 對「例外」有效、對「無限 hang」無效——逾時把 hang 轉成例外讓 fail-open 生效
    # （§1.8 / Codex 終審 P2）。0.5s 遠高於正常 LAN redis 延遲（<5ms），只在真 stall 時觸發。
    redis_socket_timeout_seconds: float = 0.5

    # asyncpg pool 大小（§3.4 PgBouncer default_pool_size=25 上游守恆：max_size*workers ≤ 25）
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    # OpenAI 設定（§0.3 DL-009：MUST 只用標準付費 API）
    openai_api_key: str = ""
    openai_model_primary: str = "gpt-5.5"
    openai_model_fallback: str = "gpt-5.4"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embed_model: str = "text-embedding-3-small"

    # 語意快取設定
    cache_local_embed_model: str = "intfloat/multilingual-e5-small"
    cache_distance_threshold: float = 0.05
    cache_ttl_seconds: int = 1209600  # 14 天
    # 語意快取啟用旗標與比對模式（DL-025）
    # exact：正規化後字面比對（v1 預設，決定性、誤命中極低、零 embedding 套件）
    # semantic：本地向量比對（fastembed，torch-free）——後續 config 開關，本 phase 未實作
    cache_enabled: bool = True
    cache_mode: str = "exact"

    # ColPali encoder 微服務（獨立容器 :8001）
    colpali_primary_url: str = "http://encoder:8001/encode_query"
    colpali_fallback_url: str = ""
    colpali_model: str = "vidore/colpali-v1.3-hf"

    # 知識庫版本與限流
    active_kb_version: int = 1
    rate_limit_per_user_min: int = 15
    rate_limit_per_user_day: int = 300
    rate_limit_global_rps: int = 20

    # 物件儲存（MinIO/S3；ingest 寫入、backend Phase 8 取頁圖）
    s3_bucket: str = "anatomy-rag-pages"
    s3_endpoint: str = "http://minio:9000"

    # Eval LLM（獨立 key，與生產分離；附錄 A）
    eval_openai_api_key: str = ""
    eval_openai_model: str = "gpt-5.5"

    # SSO（DL-016 暫緩；接回校內 SSO 時啟用）
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_discovery_url: str = ""

    # §6.7 MAY 旗標（預設關閉）：第一人稱症狀類 query 的 log 標記
    clinical_flavored_logging: bool = False

    # 觀測服務（選填）
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    sentry_dsn: str = ""

    @field_validator("database_url")
    @classmethod
    def _must_use_pgbouncer(cls, v: str) -> str:
        """解析 DSN port：應用層必須連 PgBouncer（慣例 :6432），不可直連 Postgres :5432（§0.3）。"""
        port = urlparse(v).port
        if port != 6432:
            raise ValueError(
                f"DATABASE_URL 必須連 PgBouncer :6432（目前 port={port}）；"
                "直連 Postgres :5432 僅允許用於 migrations 的 PG_DIRECT_URL"
            )
        return v

    @field_validator("pg_direct_url")
    @classmethod
    def _must_use_postgres_direct(cls, v: str) -> str:
        """解析 DSN port：migrations 專用連線必須直連 Postgres :5432（§0.3）。"""
        port = urlparse(v).port
        if port != 5432:
            raise ValueError(
                f"PG_DIRECT_URL 必須直連 Postgres :5432（migrations 唯一例外）；"
                f"不可指向 PgBouncer :6432（目前 port={port}）"
            )
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    """回傳應用程式全域設定單例；測試時直接實例化 Settings() 以搭配 monkeypatch 使用。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
