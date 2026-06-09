import base64

import pytest
from colpali_service.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthz_ready():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["ready"] is True


@pytest.mark.asyncio
async def test_encode_query_deterministic_contract():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/encode_query", json={"q": "肱二頭肌的起止點"})
        r2 = await c.post("/encode_query", json={"q": "肱二頭肌的起止點"})
    j1, j2 = r1.json(), r2.json()
    assert j1 == j2                                   # 決定性
    assert len(base64.b64decode(j1["pooled_bin"])) == 16   # bit(128)=16 bytes
    assert len(j1["tokens_bin"]) >= 1
    assert all(len(base64.b64decode(t)) == 16 for t in j1["tokens_bin"])


def test_get_encoder_real_not_implemented_yet(monkeypatch):
    """ENCODER_MOCK=false 但真實 encoder 未實作（Phase 3 前，如 make up-gpu）→
    應拋清楚的 NotImplementedError，而非難解的 ModuleNotFoundError。"""
    import colpali_service.encoder as enc

    monkeypatch.setenv("ENCODER_MOCK", "false")
    with pytest.raises(NotImplementedError, match="Phase 3"):
        enc.get_encoder()
