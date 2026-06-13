"""main.py router 掛載驗證（單元；不觸發 lifespan）。

import app 即可——不需 DB/Redis/Encoder；lifespan 只在 LifespanManager 或 uvicorn 啟動時觸發。
"""
from anatomy_backend.api.main import app


def test_chat_and_feedback_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/chat" in paths
    assert "/feedback" in paths
    assert "/healthz" in paths
    assert "/warmup" in paths
