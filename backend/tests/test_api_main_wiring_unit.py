"""main.py router 掛載驗證（單元；不觸發 lifespan）。

import app 即可——不需 DB/Redis/Encoder；lifespan 只在 LifespanManager 或 uvicorn 啟動時觸發。
"""
from anatomy_backend.api.chat import ALLOWED_LOG_STATUSES
from anatomy_backend.api.main import app


def test_chat_and_feedback_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/chat" in paths
    assert "/feedback" in paths
    assert "/healthz" in paths
    assert "/warmup" in paths


def test_chat_emitted_statuses_survive_log_query_whitelist():
    """Fix 1: chat.py が emit するすべての status 値が ALLOWED_LOG_STATUSES に存在する
    ことを保証——main._log_query の安全網に通過して DB へ正しい値が書かれる。"""
    # chat.py が実際に emit するすべての status 値
    chat_emitted = {"ok", "llm_error", "cancelled"}
    assert chat_emitted <= set(ALLOWED_LOG_STATUSES), (
        f"chat.py emits {chat_emitted - set(ALLOWED_LOG_STATUSES)} "
        f"which are NOT in ALLOWED_LOG_STATUSES—these will be silently mapped to 'ok'"
    )
