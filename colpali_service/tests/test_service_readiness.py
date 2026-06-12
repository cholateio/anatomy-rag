"""readiness 行為（§5.1 MUST：模型載入完成才 healthy）：載入前 /healthz、/encode_query 皆 503。"""
import asyncio
import threading

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_not_ready_returns_503_then_ready(monkeypatch):
    import colpali_service.main as m

    gate = threading.Event()

    class SlowEncoder:
        model = "slow-fake"
        mt_model = "fake-mt"

        def encode_query(self, q):
            from colpali_service.encoder import MockEncoder
            return MockEncoder().encode_query(q)

    def slow_get_encoder():
        gate.wait(timeout=10)
        return SlowEncoder()

    monkeypatch.setenv("ENCODER_MOCK", "false")          # 走背景執行緒載入路徑
    monkeypatch.setattr(m, "get_encoder", slow_get_encoder)
    async with LifespanManager(m.app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            r = await c.get("/healthz")
            assert r.status_code == 503 and r.json()["ready"] is False
            r = await c.post("/encode_query", json={"q": "x"})
            assert r.status_code == 503
            gate.set()                                   # 放行載入
            for _ in range(100):
                r = await c.get("/healthz")
                if r.status_code == 200:
                    break
                await asyncio.sleep(0.05)
            assert r.status_code == 200 and r.json()["ready"] is True
            r = await c.post("/encode_query", json={"q": "肱二頭肌"})
            assert r.status_code == 200 and r.json()["lang"] == "zh"


@pytest.mark.asyncio
async def test_load_failure_stays_503_with_error(monkeypatch):
    import colpali_service.main as m

    def broken():
        raise RuntimeError("weights corrupted")

    monkeypatch.setenv("ENCODER_MOCK", "false")
    monkeypatch.setattr(m, "get_encoder", broken)
    async with LifespanManager(m.app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            for _ in range(100):
                r = await c.get("/healthz")
                if r.json().get("error"):
                    break
                await asyncio.sleep(0.05)
            assert r.status_code == 503
            assert "weights corrupted" in r.json()["error"]


@pytest.mark.asyncio
async def test_stale_loader_cannot_pollute_new_lifespan(monkeypatch):
    """舊 lifespan 的慢載入完成後，不得覆寫新 lifespan 的狀態。"""
    import colpali_service.main as m

    gate = threading.Event()
    calls = {"n": 0}

    class Enc:
        mt_model = "fake-mt"

        def __init__(self, name):
            self.model = name

        def encode_query(self, q):
            from colpali_service.encoder import MockEncoder
            return MockEncoder().encode_query(q)

    def loader():
        calls["n"] += 1
        if calls["n"] == 1:
            gate.wait(timeout=10)        # 第一個 lifespan 的載入卡住
            return Enc("stale")
        return Enc("fresh")

    monkeypatch.setenv("ENCODER_MOCK", "false")
    monkeypatch.setattr(m, "get_encoder", loader)
    async with LifespanManager(m.app):
        pass                              # 舊 lifespan 結束時 loader 仍卡在 gate
    async with LifespanManager(m.app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            for _ in range(100):
                r = await c.get("/healthz")
                if r.status_code == 200:
                    break
                await asyncio.sleep(0.05)
            assert r.json()["model"] == "fresh"
            gate.set()                    # 放行舊執行緒（寫進它自己那份舊 dict）
            await asyncio.sleep(0.2)
            r = await c.get("/healthz")
            assert r.json()["model"] == "fresh"   # 新狀態不被 stale 覆寫
