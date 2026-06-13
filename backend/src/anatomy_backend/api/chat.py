"""/chat 編排與 SSE（§5.6 九步 / DL-009/012/018/021）。

可測核心 chat_event_stream（注入 deps，零框架/IO 綁定），route 層做
auth + ratelimit + 正規化 + EventSourceResponse。

流程：快取（追問跳過）→ encode（追問串接 retrieval_q）→ 檢索（連線於 retrieve_fn
內歸還，不跨串流 DL-012）→ [F5/H] async 並行抓影像 bytes + 建引文（串流前完成）→
先送 sources → 串流 LLM（user_id 入 forbidden_identifiers）→ [F8/M] 錯誤短路 →
驗證引文（data-verification）→ finish/[DONE] → [F1/H] spawn log/cache.set（
追問且通過驗證才寫 [F2/H]）。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from anatomy_backend.api import ai_stream as ais
from anatomy_backend.api.auth import User, get_current_user
from anatomy_backend.api.citations import build_citations_and_images, verify_citations
from anatomy_backend.api.schemas import NormalizedChat, normalize_chat
from anatomy_backend.cache import CacheProtocol
from anatomy_backend.encoder.client import EncoderClientProtocol
from anatomy_backend.llm.client import LLMClientProtocol
from anatomy_backend.llm.image_routing import QueryIntent, route_images
from anatomy_backend.llm.prompts import build_user_text, get_system_prompt
from anatomy_backend.retrieval.types import RetrievalResult

logger = logging.getLogger(__name__)
router = APIRouter()
_TEXT_ID = "t0"

# DB CHECK 合法值（migration 005）——與 main._log_query 共用，防止 split-brain。
ALLOWED_LOG_STATUSES = ("ok", "llm_error", "encoder_error", "retrieval_error", "cancelled")


@dataclass
class ChatDeps:
    """注入式依賴容器（可測核心，零框架綁定）。

    [F1/H] spawn: 後串流副作用（log/cache.set）MUST 透過 spawn 非同步提交，
    production 用 asyncio.create_task，測試用 collected.append。
    [F5/H] fetch_bytes: async callable（S3/MinIO）；build_citations_and_images 串流前完成。
    """

    encoder: EncoderClientProtocol
    llm: LLMClientProtocol
    cache: CacheProtocol
    retrieve_fn: Callable[..., Awaitable[list[RetrievalResult]]]
    sign_url: Callable[[str], str]
    fetch_bytes: Callable[[str], Awaitable[bytes]]  # [F5/H] async
    log_query: Callable[..., Awaitable[None]]
    spawn: Callable[[Awaitable], None]  # [F1/H] detach side-effects
    kb_version: int
    is_disconnected: Callable[[], Awaitable[bool]]
    top_n: int = 3


def _intent(normalized: NormalizedChat) -> QueryIntent:
    """v1 heuristic（OPEN DL-009；真分類器後續）。"""
    q = normalized.query.lower()
    if any(k in q for k in ("圖", "figure", "fig", "標", "示意", "構造", "解剖位置")):
        return QueryIntent.FIGURE
    return QueryIntent.PURE_TEXT


async def chat_event_stream(
    deps: ChatDeps,
    normalized: NormalizedChat,
    user: User,
) -> AsyncIterator[ServerSentEvent]:
    """§5.6 九步 SSE 產生器（純函式；注入 deps，零 I/O 框架綁定）。

    Emit order（正常路徑）：
      start → data-sources → text-start → text-delta* → text-end
      → data-verification → finish → [DONE]
    快取命中短路：
      start → data-sources → text-start → text-delta(one) → text-end → finish → [DONE]
    LLM 錯誤（[F8/M]）：
      start → data-sources → text-start → (deltas) → error → text-end → [DONE]
      （無 verification / finish）
    encoder/retrieval 失敗（P1c）：
      start → error → [DONE]
      （無 sources / verification / finish）
    """
    kb = deps.kb_version

    # ── Step 1：快取（追問 MUST NOT 查/寫，DL-021）──────────────────────────
    if not normalized.is_followup:
        cached = await deps.cache.get(normalized.query, kb, normalized.metadata_filter)
        if cached is not None:
            yield ais.sse_event(ais.start_part())
            yield ais.sse_event(ais.data_part("sources", {"sources": cached.sources}))
            yield ais.sse_event(ais.text_start_part(_TEXT_ID))
            yield ais.sse_event(ais.text_delta_part(_TEXT_ID, cached.answer))
            yield ais.sse_event(ais.text_end_part(_TEXT_ID))
            yield ais.sse_event(ais.finish_part())
            yield ais.done_event()
            # [F1/H] spawn log，永不 await
            deps.spawn(
                deps.log_query(
                    user_id=user.user_id,
                    query=normalized.query,
                    conversation_id=normalized.conversation_id,
                    cache_hit=True,
                    status="ok",
                )
            )
            return

    # ── Step 2：retrieval_q（追問串接前一問，DL-021）────────────────────────
    retrieval_q = (
        f"{normalized.prev_query}\n{normalized.query}"
        if normalized.is_followup and normalized.prev_query
        else normalized.query
    )

    # ── Step 6a：start 提前（失敗路徑也需送合規 SSE 錯誤事件，emit once here）──
    yield ais.sse_event(ais.start_part())

    # ── Step 3：encode（失敗→ encoder_error 短路，DL-012）──────────────────
    try:
        query_repr = await deps.encoder.encode_query(retrieval_q)
    except Exception:  # noqa: BLE001
        logger.exception("encode_query 失敗")
        yield ais.sse_event({"type": "error", "errorText": "服務暫時無法使用，請稍後再試"})
        yield ais.done_event()
        deps.spawn(
            deps.log_query(
                user_id=user.user_id,
                query=normalized.query,
                conversation_id=normalized.conversation_id,
                cache_hit=False,
                status="encoder_error",
            )
        )
        return

    # ── Steps 4/5：檢索 + 建引文（失敗→ retrieval_error 短路；連線於 retrieve_fn 內歸還）
    try:
        results = await deps.retrieve_fn(
            retrieval_q, query_repr, normalized.metadata_filter, kb, deps.top_n
        )
        routing = route_images(results, _intent(normalized))
        citations, images = await build_citations_and_images(
            results, routing, sign_url=deps.sign_url, fetch_bytes=deps.fetch_bytes
        )
    except Exception:  # noqa: BLE001
        logger.exception("檢索或引文建立失敗")
        yield ais.sse_event({"type": "error", "errorText": "服務暫時無法使用，請稍後再試"})
        yield ais.done_event()
        deps.spawn(
            deps.log_query(
                user_id=user.user_id,
                query=normalized.query,
                conversation_id=normalized.conversation_id,
                cache_hit=False,
                status="retrieval_error",
            )
        )
        return

    sources_payload = [c.model_dump() for c in citations]

    # ── Step 6b：sources（MUST 在第一個 text-delta 前）────────────────────
    yield ais.sse_event(ais.data_part("sources", {"sources": sources_payload}))

    # ── Step 7：串流 LLM（user_id 入 forbidden_identifiers；連線此時已歸還）──
    text_context = "\n\n".join(r.docling_md for r in results)
    system = get_system_prompt()
    user_text = build_user_text(
        text_context,
        normalized.query,
        normalized.prev_query if normalized.is_followup else None,
    )
    # [F1/H] user_id only（conversation_id 非 PII，不放入）
    forbidden = frozenset({user.user_id})
    answer_parts: list[str] = []
    yield ais.sse_event(ais.text_start_part(_TEXT_ID))
    status = "ok"
    try:
        async for delta in deps.llm.stream_complete(
            system,
            user_text,
            images,
            image_detail=routing.detail,
            forbidden_identifiers=forbidden,
        ):
            if await deps.is_disconnected():
                status = "cancelled"
                break
            answer_parts.append(delta)
            yield ais.sse_event(ais.text_delta_part(_TEXT_ID, delta))
    except Exception:  # noqa: BLE001  [F8/M] LLM 串流失敗契約
        logger.exception("LLM 串流失敗")
        yield ais.sse_event({"type": "error", "errorText": "生成失敗，請重試"})
        status = "llm_error"
        yield ais.sse_event(ais.text_end_part(_TEXT_ID))
        yield ais.done_event()
        # [F1/H] spawn log，MUST NOT emit finish/verification
        deps.spawn(
            deps.log_query(
                user_id=user.user_id,
                query=normalized.query,
                conversation_id=normalized.conversation_id,
                cache_hit=False,
                status="llm_error",
                model_used=None,
            )
        )
        return  # 跳過 verification 和 finish

    yield ais.sse_event(ais.text_end_part(_TEXT_ID))

    # ── Step 8：引文驗證 + data-verification（前端 banner / 快取守門）────────
    answer = "".join(answer_parts)
    verification = verify_citations(answer, results)
    yield ais.sse_event(
        ais.data_part(
            "verification",
            {
                "verified": verification.all_grounded,
                "has_citations": verification.has_citations,
                "unverified": verification.unverified,
            },
        )
    )

    # ── Step 9：finish + [DONE]──────────────────────────────────────────────
    yield ais.sse_event(ais.finish_part())
    yield ais.done_event()

    # [F1/H] spawn 副作用，永不 await（SSE 已送完）
    deps.spawn(
        deps.log_query(
            user_id=user.user_id,
            query=normalized.query,
            conversation_id=normalized.conversation_id,
            cache_hit=False,
            status=status,
            model_used=None,
        )
    )
    # [F2/H] cache.set 僅在非追問 + ok + all_grounded（防快取偽造引文答案）
    if (not normalized.is_followup) and status == "ok" and verification.all_grounded:
        deps.spawn(
            deps.cache.set(
                normalized.query, answer, sources_payload, kb,
                verified=True, metadata_filter=normalized.metadata_filter,
            )
        )


@router.post("/chat")
async def chat(
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> EventSourceResponse:
    """§5.6 /chat：auth → ratelimit → 正規化（[F6/M] ValueError→400）→ SSE 串流。"""
    body = await request.json()
    # [F6/M] ValueError from normalize_chat → HTTP 400
    try:
        normalized = normalize_chat(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    limiter = request.app.state.ratelimiter
    rl = await limiter.check(user_id=user.user_id, is_admin=user.is_admin)
    if not rl.allowed:
        raise HTTPException(
            status_code=429,
            detail="請求過於頻繁，請稍後再試",
            headers={"Retry-After": str(rl.retry_after)},
        )
    deps: ChatDeps = request.app.state.build_chat_deps(request)
    return EventSourceResponse(
        (ev async for ev in chat_event_stream(deps, normalized, user)),
        headers=ais.UI_MESSAGE_STREAM_HEADERS,
        ping=3600,
    )
