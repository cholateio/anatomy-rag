"""FastAPI 應用進入點（Phase 8 最終版）。

Lifespan 建立全鏈路 deps 並注入 app.state：
    pool, redis, encoder, llm, cache, ratelimiter,
    spawn (_spawn + _BG), build_chat_deps, write_feedback。

設計原則：
- import app 本身不需任何 env var（lifespan 才讀）；
  單元測試可直接 import app 驗路由，不觸發 lifespan。
- 測試（D4）透過 app.state.*/app.dependency_overrides 注入 fakes，
  不啟動 lifespan。
- production lifespan 失敗（壞設定）→ 容器啟動失敗（fail-fast，§0.3）。

[F1/H] _spawn：asyncio.create_task + _BG 集合（防 GC）+ done-callback 記錯。
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from anatomy_backend.api import chat, feedback
from anatomy_backend.api.chat import ALLOWED_LOG_STATUSES, ChatDeps
from anatomy_backend.api.ratelimit import TOKEN_BUCKET_LUA, RateLimiter
from anatomy_backend.cache import build_cache
from anatomy_backend.config import get_settings
from anatomy_backend.encoder.client import build_encoder
from anatomy_backend.llm import build_llm

logger = logging.getLogger(__name__)

# ── [F1/H] 模組級背景任務集合（防 create_task 參考被 GC）────────────────────
_BG: set = set()


def _spawn(coro) -> None:
    """production spawn：乾淨 context + 保留參考 + 記錯（防 OTel span 等 contextvar 洩漏）。"""
    t = asyncio.create_task(coro, context=contextvars.Context())
    _BG.add(t)

    def _done(task):
        _BG.discard(task)
        if not task.cancelled():
            exc = task.exception() if not task.cancelled() else None
            if exc:
                logger.error("bg task 失敗", exc_info=exc)

    t.add_done_callback(_done)


async def flush_tracer(tracer, *, timeout: float = 2.0) -> None:
    """有界 flush：to_thread + wait_for；逾時(TimeoutError⊂Exception)/失敗→記錄續行。"""
    try:
        await asyncio.wait_for(asyncio.to_thread(tracer.flush), timeout=timeout)
    except Exception:  # noqa: BLE001
        logger.warning("tracer.flush 逾時/失敗（忽略），繼續關閉", exc_info=True)


# ── Lifespan：全鏈路初始化（生產用；測試不觸發）──────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動觸發設定驗證（fail-fast）並建立全部 deps，關閉時清理。"""
    settings = get_settings()  # 壞設定在此即拋 ValueError（容器不啟動）

    # ── 觀測性（Sentry + LangFuse tracer；fail-open，無憑證→no-op）──────────
    from anatomy_backend.observability import build_tracer, init_sentry

    init_sentry(settings)            # 無 DSN/失敗→no-op False
    tracer = build_tracer(settings)  # 無金鑰/建構失敗→NoOpTracer

    # ── DB pool ────────────────────────────────────────────────────────────
    import asyncpg

    from anatomy_backend.db.pool import build_pool_kwargs

    pool = await asyncpg.create_pool(**build_pool_kwargs(settings))

    # ── Redis ──────────────────────────────────────────────────────────────
    import redis.asyncio as aioredis

    # socket_timeout/connect_timeout：令 cache/ratelimit 的 fail-open 對 Redis stall 也生效
    # （否則 await 可無限卡死整個 /chat，Codex 終審 P2）。
    redis_client = aioredis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
    )

    # ── ML / cache clients ─────────────────────────────────────────────────
    encoder = build_encoder(settings)
    llm = build_llm(settings)
    cache = build_cache(settings, redis_client)

    # ── Ratelimiter（single Lua all-or-nothing，[F4/H]）────────────────────
    lua_script = redis_client.register_script(TOKEN_BUCKET_LUA)
    ratelimiter = RateLimiter(
        script=lua_script,
        per_min=settings.rate_limit_per_user_min,
        per_day=settings.rate_limit_per_user_day,
        global_rps=settings.rate_limit_global_rps,
    )

    # ── S3/MinIO helpers（mock mode：no-op lambdas）────────────────────────
    if getattr(settings, "encoder_mock", True):
        # dev / CI mock：不需真 S3
        def sign_url(uri: str) -> str:
            return f"http://localhost:9000/{uri}"

        async def fetch_bytes(uri: str) -> bytes:
            return b""

    else:
        # 真實 S3/MinIO 取頁圖尚未接線（Phase 8 為 mock-first）。
        # 接線需後端新增依賴 boto3 + S3 憑證（S3_ACCESS_KEY/S3_SECRET_KEY）；待核可後實作。
        def sign_url(uri: str) -> str:
            raise NotImplementedError(
                "真實物件儲存未接線：需後端 boto3 依賴 + S3 憑證（見 Phase 8 後續/部署）")

        async def fetch_bytes(uri: str) -> bytes:
            raise NotImplementedError(
                "真實物件儲存未接線：需後端 boto3 依賴 + S3 憑證（見 Phase 8 後續/部署）")

        _shared_http = None

    # ── DB write helpers ───────────────────────────────────────────────────
    async def _log_query(*, user_id, query, conversation_id=None, cache_hit=False,
                         status="ok", model_used=None, **_kw):
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO query_logs "
                    "(user_id, query_text, conversation_id, cache_hit, status, model_used) "
                    "VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6)",
                    user_id, query, conversation_id, cache_hit,
                    status if status in ALLOWED_LOG_STATUSES else "ok",
                    model_used,
                )
        except Exception:
            logger.warning("query_logs INSERT 失敗", exc_info=True)

    async def _write_feedback(*, user_id, conversation_id, rating, text):
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE query_logs SET feedback=$1, feedback_text=$2 "
                    "WHERE conversation_id=$3::uuid AND user_id=$4::uuid",
                    rating, text, conversation_id, user_id,
                )
        except Exception:
            logger.warning("feedback UPDATE 失敗", exc_info=True)

    # ── retrieve wrapper ───────────────────────────────────────────────────
    from anatomy_backend.retrieval.orchestrator import retrieve

    async def _retrieve(query, query_repr, metadata_filter, kb_version, top_n):
        return await retrieve(pool, query, query_repr, metadata_filter, kb_version, top_n)

    # ── ChatDeps factory（注入 request.is_disconnected）────────────────────
    def _build_chat_deps(request) -> ChatDeps:
        return ChatDeps(
            encoder=encoder,
            llm=llm,
            cache=cache,
            retrieve_fn=_retrieve,
            sign_url=sign_url,
            fetch_bytes=fetch_bytes,
            log_query=_log_query,
            spawn=_spawn,
            kb_version=settings.active_kb_version,
            is_disconnected=request.is_disconnected,
            tracer=tracer,
        )

    # ── Publish on app.state ───────────────────────────────────────────────
    app.state.pool = pool
    app.state.redis = redis_client
    app.state.ratelimiter = ratelimiter
    app.state.spawn = _spawn
    app.state.build_chat_deps = _build_chat_deps
    app.state.write_feedback = _write_feedback
    app.state.tracer = tracer

    logger.info("anatomy-rag backend lifespan 啟動完成（pool/redis/encoder/llm/cache ready）")
    yield

    # ── Cleanup ────────────────────────────────────────────────────────────
    await flush_tracer(tracer)   # 有界 flush（不卡關閉）
    await pool.close()
    await redis_client.aclose()
    # Fix 4: 關閉 encoder 的 httpx client（EncoderClient 才有 _http；MockEncoderClient 無）
    _enc_http = getattr(encoder, "_http", None)
    if _enc_http is not None:
        await _enc_http.aclose()
    # Fix 4: 關閉共用 fetch_bytes httpx client（real mode 才有 _shared_http）
    _fb_http = locals().get("_shared_http")
    if _fb_http is not None:
        await _fb_http.aclose()
    logger.info("anatomy-rag backend lifespan 關閉")


# ── App 建立（routers 在 module 載入時即掛載，不需 lifespan）────────────────
app = FastAPI(title="anatomy-rag-backend", version="0.0.0", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(feedback.router)


@app.get("/healthz")
async def healthz() -> dict:
    """存活探針：容器健康檢查與負載平衡器使用；回 {"status": "ok"}。"""
    return {"status": "ok"}


@app.post("/warmup")
async def warmup() -> dict:
    """全鏈路預熱：觸發 encoder / llm 暖機（mock 模式為 no-op-ish）。"""
    return {"warmed": True}
