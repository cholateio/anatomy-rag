"""端到端 SSE wire 對照 golden（純 mock；無 DB/Redis/lifespan）。

[F7/M] 斷言實際 wire bytes：
  - 每 frame 為 `data: <compact-json>\n\n`（分隔符 \\n\\n，非 \\r\\n）
  - text-start / text-delta / text-end / finish / start frame 逐字比對
  - data-sources 驗 compact 形狀 + 關鍵欄位（transient、page），不鎖 snippet 全文
  - 終止為字面 `data: [DONE]\\n\\n`
  - 標頭 x-vercel-ai-ui-message-stream == v1

設計：httpx.ASGITransport（無 LifespanManager → lifespan 不跑）；
  dependency_overrides 注入 User；app.state 手動設定 ratelimiter / build_chat_deps。
"""
from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from anatomy_backend.api.auth import User, get_current_user
from anatomy_backend.api.chat import ChatDeps
from anatomy_backend.api.ratelimit import RateLimitResult
from anatomy_backend.cache import NoOpCache
from anatomy_backend.encoder.client import MockEncoderClient
from anatomy_backend.llm.mock import MockLLMClient
from anatomy_backend.retrieval.types import RetrievalResult

# ── 固定測試 tokens（與 golden 對齊）────────────────────────────────────────
_TOKENS = ["肱二頭肌", "起於喙突 [Gray, p.812, Fig.7-23]。"]


def _golden_result() -> RetrievalResult:
    return RetrievalResult(
        uuid4(),
        0.9,
        "Gray",
        "42",
        812,
        "s3://b/p812.png",
        "肱二頭肌起於喙突。" * 20,
        {"page_type": "figure_heavy", "figures": ["Fig.7-23"]},
    )


async def _never_disc() -> bool:
    return False


async def _fetch_bytes(uri: str) -> bytes:
    return b"PNG"


async def _retrieve(query, query_repr, metadata_filter, kb_version, top_n):
    return [_golden_result()]


async def _log(**kw):
    pass


# ── SSE 解析工具 ──────────────────────────────────────────────────────────────


def _parse_sse_payloads(text: str) -> list[str]:
    """raw response text → data payload 字串列表（保序）。"""
    payloads: list[str] = []
    for block in text.split("\n\n"):
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith("data:"):
            payloads.append(stripped[len("data:"):].strip())
    return payloads


def _parse_sse_parts(text: str) -> list:
    """payloads → parsed objects（[DONE] 以字串保留）。"""
    parts = []
    for p in _parse_sse_payloads(text):
        parts.append("[DONE]" if p == "[DONE]" else json.loads(p))
    return parts


# ── 端到端 SSE golden 測試 ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _cleanup_app_state():
    """每測試後還原 app.dependency_overrides 與 app.state（防測試污染）。"""
    from anatomy_backend.api.main import app

    original_overrides = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original_overrides)
    # 清理手動設定的 state 欄位
    for attr in ("ratelimiter", "build_chat_deps"):
        try:
            delattr(app.state, attr)
        except AttributeError:
            pass


async def test_end_to_end_sse_wire_matches_golden_contract():
    """[F7] 實際 wire bytes 驗證：frame 格式、順序、標頭、[DONE]。"""
    from anatomy_backend.api.main import app

    # ── 注入 fakes（不啟動 lifespan）────────────────────────────────────────
    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    class _FakeRateLimiter:
        async def check(self, *, user_id, is_admin):
            return RateLimitResult(allowed=True, retry_after=0)

    app.state.ratelimiter = _FakeRateLimiter()

    def _build_deps(req) -> ChatDeps:
        return ChatDeps(
            encoder=MockEncoderClient(),
            llm=MockLLMClient(tokens=_TOKENS),
            cache=NoOpCache(),
            retrieve_fn=_retrieve,
            sign_url=lambda u: f"https://signed/{u}",
            fetch_bytes=_fetch_bytes,
            log_query=_log,
            spawn=lambda coro: coro.close(),  # no side-effect assertion needed
            kb_version=1,
            is_disconnected=_never_disc,
        )

    app.state.build_chat_deps = _build_deps

    # ── 發送請求並讀取全部 SSE ─────────────────────────────────────────────
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "parts": [{"type": "text", "text": "肱二頭肌的構造？"}],
                    }
                ]
            },
        )

    # ── 狀態碼與標頭 ───────────────────────────────────────────────────────
    assert resp.status_code == 200
    assert resp.headers.get("x-vercel-ai-ui-message-stream") == "v1"
    assert resp.headers.get("content-type", "").startswith("text/event-stream")

    raw = resp.text

    # [F7] 分隔符為 \n\n 而非 \r\n
    assert "\r\n" not in raw, "SSE frame 分隔符必須為 \\n\\n 非 \\r\\n"

    # [F7] 每個 data: 行格式 —— 非空 block 必須以 "data: " 開頭
    non_empty_blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
    sse_blocks = [b for b in non_empty_blocks if b.startswith("data:")]
    # ping / comment 行忽略（: ping ...），只驗 data 行
    # start, sources, text-start, 2×delta, text-end, verification, finish
    assert len(sse_blocks) >= 8, "應有至少 8 個 data 幀"

    # [F7] 靜態 frame 逐字比對
    def _frame(obj: dict) -> str:
        return "data: " + json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    assert _frame({"type": "start"}) in raw
    assert _frame({"type": "text-start", "id": "t0"}) in raw
    assert _frame({"type": "text-delta", "id": "t0", "delta": "肱二頭肌"}) in raw
    expected_delta2 = _frame(
        {"type": "text-delta", "id": "t0", "delta": "起於喙突 [Gray, p.812, Fig.7-23]。"}
    )
    assert expected_delta2 in raw
    assert _frame({"type": "text-end", "id": "t0"}) in raw
    assert _frame({"type": "finish"}) in raw

    # [F7] 終止為字面 data: [DONE]\n\n
    assert raw.endswith("data: [DONE]\n\n"), f"終止符不符：最後 50 chars = {raw[-50:]!r}"

    # [F7] 解析驗 data-sources 形狀（不鎖 snippet 全文）
    parts = _parse_sse_parts(raw)
    types = [p if p == "[DONE]" else p["type"] for p in parts]

    src = next(p for p in parts if isinstance(p, dict) and p["type"] == "data-sources")
    assert src.get("transient") is True
    assert src["data"]["sources"][0]["page"] == 812

    # data-sources 在第一個 text-delta 前
    assert types.index("data-sources") < types.index("text-delta")

    # data-verification 在 finish 前
    ver = next(p for p in parts if isinstance(p, dict) and p["type"] == "data-verification")
    assert ver["data"]["verified"] is True
    assert types.index("data-verification") < types.index("finish")

    # [DONE] 在最後
    assert parts[-1] == "[DONE]"


async def test_sse_normalize_chat_error_returns_400():
    """[F6/M] normalize_chat ValueError → HTTP 400。"""
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    class _FakeRateLimiter:
        async def check(self, *, user_id, is_admin):
            return RateLimitResult(allowed=True, retry_after=0)

    app.state.ratelimiter = _FakeRateLimiter()
    app.state.build_chat_deps = lambda req: None  # never reached

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        # 送空 messages → normalize_chat 拋 ValueError
        resp = await ac.post("/chat", json={"messages": []})

    assert resp.status_code == 400


async def test_sse_ratelimit_returns_429():
    """rate limiter 拒絕 → HTTP 429 + Retry-After 標頭。"""
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    class _DenyLimiter:
        async def check(self, *, user_id, is_admin):
            return RateLimitResult(allowed=False, retry_after=3)

    app.state.ratelimiter = _DenyLimiter()
    app.state.build_chat_deps = lambda req: None  # never reached

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "q"}]},
        )

    assert resp.status_code == 429
    assert resp.headers.get("retry-after") == "3"
