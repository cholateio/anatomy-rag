import anatomy_backend.config as config_module
import pytest
from anatomy_backend.api.main import app
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthz_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_warmup_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/warmup")
    assert r.status_code == 200 and r.json() == {"warmed": True}


@pytest.mark.asyncio
async def test_startup_validates_settings_fail_fast(monkeypatch):
    """lifespan 啟動時 MUST 觸發設定驗證：DATABASE_URL 直連 :5432 應使啟動失敗（fail-fast，§0.3）。

    純 ASGITransport 不會跑 lifespan，故用 LifespanManager 真正觸發 startup；
    驗證 get_settings() 確實在啟動時被呼叫（否則錯誤設定會以 healthy 假象掩蓋）。
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres:5432/db")  # 違規：直連 Postgres
    monkeypatch.setenv("PG_DIRECT_URL", "postgresql://u:p@postgres:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    config_module._settings = None  # 重置單例，強制以上述 env 重讀
    try:
        with pytest.raises(ValueError, match="6432"):
            async with LifespanManager(app):
                pass
    finally:
        config_module._settings = None  # 還原，避免污染其他測試
