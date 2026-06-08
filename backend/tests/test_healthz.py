import pytest
from anatomy_backend.api.main import app
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
