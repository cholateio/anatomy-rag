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
    """舊 lifespan 的慢載入完成後，不得覆寫新 lifespan 的狀態。

    以 Event 顯式同步（非排程順序/sleep）：started 保證舊 loader 已起跑並 park；
    最終斷言前輪詢舊 dict，確保舊執行緒的 stale 寫入已落地。
    """
    import colpali_service.main as m

    gate = threading.Event()
    started = threading.Event()
    first = threading.Event()   # 第一個取得者=舊 lifespan 的 loader（test-and-set，無排程假設）

    class Enc:
        mt_model = "fake-mt"

        def __init__(self, name):
            self.model = name

        def encode_query(self, q):
            from colpali_service.encoder import MockEncoder
            return MockEncoder().encode_query(q)

    def loader():
        if not first.is_set():
            first.set()
            started.set()
            gate.wait(timeout=10)        # 舊 lifespan 的載入 park 在此
            return Enc("stale")
        return Enc("fresh")

    monkeypatch.setenv("ENCODER_MOCK", "false")
    monkeypatch.setattr(m, "get_encoder", loader)
    async with LifespanManager(m.app):
        assert started.wait(timeout=5)    # 舊 loader 確定已起跑並 park，才結束舊 lifespan
    # 監看舊執行緒何時把 stale 寫進它那份舊 dict
    old_state = m.app.state.enc

    async with LifespanManager(m.app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            for _ in range(100):
                r = await c.get("/healthz")
                if r.status_code == 200:
                    break
                await asyncio.sleep(0.05)
            assert r.json()["model"] == "fresh"
            gate.set()                    # 放行舊執行緒（寫進它自己那份舊 dict）
            for _ in range(100):          # 等 stale 寫入落地（觀察舊 dict，非 sleep 猜時間）
                if old_state["encoder"] is not None and old_state["encoder"].model == "stale":
                    break
                await asyncio.sleep(0.01)
            assert old_state["encoder"].model == "stale"   # mutation 偵測力：stale 確實寫完了
            r = await c.get("/healthz")
            assert r.json()["model"] == "fresh"   # 新狀態不被 stale 覆寫


def test_warmup_query_exercises_mt_path():
    """warmup 字串必須含非 glossary 的 CJK 段，否則 Marian 永遠冷啟（Codex 終審 P2）。"""
    from colpali_service.main import WARMUP_QUERY
    from colpali_service.translate import LocalTranslator, load_glossary

    calls: list[list[str]] = []

    def spy(texts):
        calls.append(texts)
        return ["warm segment"] * len(texts)

    tr = LocalTranslator(mt_fn=spy, mt_model_name="f",
                         glossary=load_glossary(), t2s_fn=lambda s: s)
    r = tr.translate(WARMUP_QUERY)
    assert calls, "warmup query 未觸發 MT——Marian 會保持冷啟"
    assert r.translated_q is not None
