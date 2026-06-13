"""chat_event_stream 單元測試（§5.6 九步 + F1-F8 對抗式審查修正）。

全程 mock（MockEncoderClient / MockLLMClient / 假 retrieve / NoOpCache）；零 DB/Redis/OpenAI 呼叫。
[F1] spawn 收集 coro → asyncio.gather 確保決定性副作用斷言。
[F2] cache.set 僅在 all_grounded=True 時呼叫（spy 斷言）。
[F5] fetch_bytes 為 async。
[F8] LLM 例外 → error 事件、無 finish、[DONE] 收尾、log status=llm_error（DB 合法值）。
斷線 → log status=cancelled（DB 合法值）。
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from anatomy_backend.api.auth import User
from anatomy_backend.api.chat import ALLOWED_LOG_STATUSES, ChatDeps, chat_event_stream
from anatomy_backend.api.schemas import NormalizedChat
from anatomy_backend.cache import CachedAnswer, NoOpCache
from anatomy_backend.encoder.client import MockEncoderClient
from anatomy_backend.llm.mock import MockLLMClient
from anatomy_backend.retrieval.types import RetrievalResult


def _result(page: int = 812, pt: str = "figure_heavy") -> RetrievalResult:
    return RetrievalResult(
        uuid4(),
        0.9,
        "Gray",
        "42",
        page,
        f"s3://b/p{page}.png",
        "肱二頭肌起於喙突。" * 20,
        {"page_type": pt, "figures": ["Fig.7-23"]},
    )


async def _never_disconnected() -> bool:
    return False


async def _fetch_bytes(uri: str) -> bytes:  # [F5/H] async fetch_bytes
    return b"PNG"


def _deps(
    *,
    llm=None,
    cache=None,
    results=None,
    logs=None,
    collected=None,
    is_disconnected=None,
) -> ChatDeps:
    # If caller supplies collected list, append for later gather; else close to suppress
    # "coroutine was never awaited" RuntimeWarning from Python GC.
    if collected is not None:

        def _spawn(coro):
            collected.append(coro)

    else:

        def _spawn(coro):
            coro.close()

    async def _retrieve(query, query_repr, metadata_filter, kb_version, top_n):
        return results if results is not None else [_result()]

    async def _log(**kw):
        if logs is not None:
            logs.append(kw)

    return ChatDeps(
        encoder=MockEncoderClient(),
        llm=llm or MockLLMClient(tokens=["肱二頭肌", "起於喙突", " [Gray, p.812, Fig.7-23]。"]),
        cache=cache or NoOpCache(),
        retrieve_fn=_retrieve,
        sign_url=lambda u: f"https://signed/{u}",
        fetch_bytes=_fetch_bytes,
        log_query=_log,
        spawn=_spawn,  # [F1/H] collect or close, never await
        kb_version=1,
        is_disconnected=is_disconnected or _never_disconnected,
    )


def _norm(
    query: str = "肱二頭肌起點？",
    prev: str | None = None,
    followup: bool = False,
) -> NormalizedChat:
    return NormalizedChat(
        query=query,
        prev_query=prev,
        metadata_filter=None,
        conversation_id=None,
        is_followup=followup,
    )


async def _collect(agen):
    return [ev async for ev in agen]


def _json_parts(events):
    out = []
    for ev in events:
        if ev.data == "[DONE]":
            out.append("[DONE]")
            continue
        out.append(json.loads(ev.data))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 基本事件序列
# ──────────────────────────────────────────────────────────────────────────────


async def test_event_sequence_sources_before_first_delta():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    types = [p if p == "[DONE]" else p["type"] for p in parts]
    assert types[0] == "start"
    i_src = next(i for i, t in enumerate(types) if t == "data-sources")
    i_delta = next(i for i, t in enumerate(types) if t == "text-delta")
    assert i_src < i_delta  # sources 必在第一個 delta 前
    assert types[-1] == "[DONE]"
    assert "finish" in types and "text-end" in types


async def test_sources_payload_has_page_citations():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    src = next(p for p in parts if isinstance(p, dict) and p["type"] == "data-sources")
    assert src["transient"] is True
    assert src["data"]["sources"][0]["page"] == 812


async def test_deltas_concatenate_to_answer():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    text = "".join(p["delta"] for p in parts if isinstance(p, dict) and p["type"] == "text-delta")
    assert "肱二頭肌" in text


async def test_verification_data_part_emitted_before_finish():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    types = [p["type"] for p in parts if isinstance(p, dict)]
    assert "data-verification" in types
    assert types.index("data-verification") < types.index("finish")


# ──────────────────────────────────────────────────────────────────────────────
# 追問 / PII 護欄
# ──────────────────────────────────────────────────────────────────────────────


async def test_followup_skips_cache_and_passes_prev_query():
    """追問：MUST 不查快取；user-message 帶前一問；user_id 在 forbidden_identifiers。"""
    captured: dict = {}

    class _SpyLLM(MockLLMClient):
        async def stream_complete(
            self, system, user, images, *, image_detail="high", forbidden_identifiers=frozenset()
        ):
            captured["user"] = user
            captured["forbidden"] = forbidden_identifiers
            async for t in super().stream_complete(
                system,
                user,
                images,
                image_detail=image_detail,
                forbidden_identifiers=forbidden_identifiers,
            ):
                yield t

    class _BoomCache(NoOpCache):
        async def get(self, q, kb):
            raise AssertionError("追問不得查快取")

    # collected=None → spawn closes coros immediately (no unawaited-coro warnings)
    deps = _deps(llm=_SpyLLM(tokens=["x"]), cache=_BoomCache())
    user = User("u1", False)
    await _collect(chat_event_stream(deps, _norm(prev="肱二頭肌起點？", followup=True), user))
    assert "前一問：肱二頭肌起點？" in captured["user"]
    assert "u1" in captured["forbidden"]  # PII 護欄


async def test_pii_user_id_in_forbidden_identifiers():
    captured: dict = {}

    class _SpyLLM(MockLLMClient):
        async def stream_complete(
            self, system, user, images, *, image_detail="high", forbidden_identifiers=frozenset()
        ):
            captured["forbidden"] = forbidden_identifiers
            async for t in super().stream_complete(
                system,
                user,
                images,
                image_detail=image_detail,
                forbidden_identifiers=forbidden_identifiers,
            ):
                yield t

    deps = _deps(llm=_SpyLLM(tokens=["x"]))  # no collected → coros closed immediately
    await _collect(chat_event_stream(deps, _norm(), User("stud-42", False)))
    assert "stud-42" in captured["forbidden"]


# ──────────────────────────────────────────────────────────────────────────────
# 快取命中
# ──────────────────────────────────────────────────────────────────────────────


async def test_cache_hit_short_circuits_with_sources_and_done():
    class _HitCache(NoOpCache):
        async def get(self, q, kb):
            return CachedAnswer(
                answer="快取答案 [Gray, p.812]。",
                sources=[
                    {
                        "book_title": "Gray",
                        "edition": "42",
                        "page": 812,
                        "figure": None,
                        "image_url": "u",
                        "snippet": "s",
                        "score": 0.9,
                    }
                ],
            )

    logs: list = []
    collected: list = []
    deps = _deps(cache=_HitCache(), logs=logs, collected=collected)
    parts = _json_parts(
        await _collect(chat_event_stream(deps, _norm(), User("u1", False)))
    )
    types = [p if p == "[DONE]" else p["type"] for p in parts]
    assert "data-sources" in types and types[-1] == "[DONE]"
    text = "".join(
        p["delta"] for p in parts if isinstance(p, dict) and p["type"] == "text-delta"
    )
    assert text == "快取答案 [Gray, p.812]。"
    # [F1] flush spawned coros then assert side effects
    await asyncio.gather(*collected)
    assert logs and logs[-1].get("cache_hit") is True


# ──────────────────────────────────────────────────────────────────────────────
# 客戶端斷線
# ──────────────────────────────────────────────────────────────────────────────


async def test_disconnect_stops_streaming_early():
    state = {"n": 0}

    async def _disc() -> bool:
        state["n"] += 1
        return state["n"] > 1  # 第二次檢查時斷線

    async def _fetch(uri: str) -> bytes:
        return b"PNG"

    async def _retrieve(query, query_repr, metadata_filter, kb_version, top_n):
        return [_result()]

    async def _log(**kw):
        pass

    # spawn closes coros immediately; disconnect test doesn't check side effects
    deps = ChatDeps(
        encoder=MockEncoderClient(),
        llm=MockLLMClient(tokens=["a", "b", "c", "d"]),
        cache=NoOpCache(),
        retrieve_fn=_retrieve,
        sign_url=lambda u: f"https://signed/{u}",
        fetch_bytes=_fetch,
        log_query=_log,
        spawn=lambda coro: coro.close(),
        kb_version=1,
        is_disconnected=_disc,
    )
    parts = _json_parts(
        await _collect(chat_event_stream(deps, _norm(), User("u1", False)))
    )
    deltas = [p for p in parts if isinstance(p, dict) and p["type"] == "text-delta"]
    assert len(deltas) < 4  # 提前中止


# ──────────────────────────────────────────────────────────────────────────────
# [F2] cache.set 僅在 all_grounded=True 時呼叫
# ──────────────────────────────────────────────────────────────────────────────


async def test_f2_cache_set_not_called_when_no_citation():
    """[F2] 無引文答案 → all_grounded=False → cache.set 絕不被呼叫。"""

    class _SpyCache(NoOpCache):
        def __init__(self):
            self.set_called = False

        async def set(self, query, answer, sources, kb, *, verified):
            self.set_called = True

    spy = _SpyCache()
    collected: list = []
    # 無引文 token
    deps = _deps(llm=MockLLMClient(tokens=["無引文答案"]), cache=spy, collected=collected)
    await _collect(chat_event_stream(deps, _norm(), User("u1", False)))
    await asyncio.gather(*collected)
    assert not spy.set_called


async def test_f2_cache_set_not_called_when_citation_unverified():
    """[F2] 偽造引文（頁碼不在 retrieved）→ all_grounded=False → cache.set 絕不被呼叫。"""

    class _SpyCache(NoOpCache):
        def __init__(self):
            self.set_called = False

        async def set(self, query, answer, sources, kb, *, verified):
            self.set_called = True

    spy = _SpyCache()
    collected: list = []
    # p.999 不在 retrieved（retrieved 只有 p.812）
    deps = _deps(
        llm=MockLLMClient(tokens=["答案 [Gray, p.999]。"]), cache=spy, collected=collected
    )
    await _collect(chat_event_stream(deps, _norm(), User("u1", False)))
    await asyncio.gather(*collected)
    assert not spy.set_called


async def test_f2_cache_set_called_when_all_grounded():
    """[F2] 正確引文 → all_grounded=True → cache.set 被呼叫一次。"""

    class _SpyCache(NoOpCache):
        def __init__(self):
            self.set_count = 0

        async def set(self, query, answer, sources, kb, *, verified):
            self.set_count += 1

    spy = _SpyCache()
    collected: list = []
    deps = _deps(
        llm=MockLLMClient(tokens=["肱二頭肌", "起於喙突", " [Gray, p.812, Fig.7-23]。"]),
        cache=spy,
        collected=collected,
    )
    await _collect(chat_event_stream(deps, _norm(), User("u1", False)))
    await asyncio.gather(*collected)
    assert spy.set_count == 1


# ──────────────────────────────────────────────────────────────────────────────
# [F8] LLM 串流失敗契約
# ──────────────────────────────────────────────────────────────────────────────


async def test_f8_llm_stream_error_emits_error_no_finish_ends_with_done():
    """[F8] LLM 串流例外 → error 事件、無 finish、[DONE] 收尾、log status=error。"""

    class _MidStreamErrorLLM:
        async def stream_complete(
            self, system, user, images, *, image_detail="high", forbidden_identifiers=frozenset()
        ):
            yield "partial"
            raise RuntimeError("LLM crashed mid-stream")

    logs: list = []
    collected: list = []
    deps = _deps(llm=_MidStreamErrorLLM(), logs=logs, collected=collected)
    parts = _json_parts(
        await _collect(chat_event_stream(deps, _norm(), User("u1", False)))
    )
    await asyncio.gather(*collected)

    types = [p if p == "[DONE]" else p.get("type") for p in parts]
    assert "error" in types
    assert "finish" not in types
    assert parts[-1] == "[DONE]"
    # Fix 1: LLM 失敗必須記 "llm_error"（DB CHECK 合法值），不可記 "error"
    assert logs and logs[-1]["status"] == "llm_error"


# ── Fix 1: DB 合法 status 常數與 chat.py emit 一致性 ─────────────────────────


def test_allowed_log_statuses_contains_emitted_literals():
    """ALLOWED_LOG_STATUSES 包含 chat.py 所有可能 emit 的 status 值——防 split-brain。"""
    assert "llm_error" in ALLOWED_LOG_STATUSES   # LLM 串流例外
    assert "cancelled" in ALLOWED_LOG_STATUSES   # 客戶端斷線
    assert "ok" in ALLOWED_LOG_STATUSES          # 正常路徑
    assert "encoder_error" in ALLOWED_LOG_STATUSES
    assert "retrieval_error" in ALLOWED_LOG_STATUSES


# ── 客戶端斷線後 log status=cancelled ────────────────────────────────────────


async def test_disconnect_log_status_is_cancelled():
    """客戶端斷線 → log status='cancelled'（DB CHECK 合法值，非 'client_disconnect'）。"""
    state = {"n": 0}

    async def _disc() -> bool:
        state["n"] += 1
        return state["n"] > 1  # 第二次檢查時斷線

    logs: list = []
    collected: list = []

    async def _retrieve_one(query, query_repr, metadata_filter, kb_version, top_n):
        return [_result()]

    async def _log(**kw):
        logs.append(kw)

    deps = ChatDeps(
        encoder=MockEncoderClient(),
        llm=MockLLMClient(tokens=["a", "b", "c", "d"]),
        cache=NoOpCache(),
        retrieve_fn=_retrieve_one,
        sign_url=lambda u: f"https://signed/{u}",
        fetch_bytes=_fetch_bytes,
        log_query=_log,
        spawn=lambda coro: collected.append(coro),
        kb_version=1,
        is_disconnected=_disc,
    )
    await _collect(chat_event_stream(deps, _norm(), User("u1", False)))
    await asyncio.gather(*collected)
    assert logs and logs[-1]["status"] == "cancelled"
