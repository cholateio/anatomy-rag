"""Sentry 錯誤回報 + before_send 結構 allowlist 脫敏（§6.5 / D-M / DL-026）。

D-M：不做內容層 PHI 攔截，改在外送 Sentry 時**結構 allowlist**——只保留明確核准的非自由文字
欄位，其餘（user/tags/extra/breadcrumbs/request/message/logentry/未知頂層）整塊丟棄；例外僅留
type+stacktrace 結構（去 value/vars/context_line）；contexts 僅留核准型別並再 key-scrub。
空 DSN→no-op；init 失敗→fail-open False；非 dict/出錯→回 None 丟棄該 event。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REDACTED = "[redacted]"

_SENSITIVE_SUBSTRINGS = (
    "query", "prompt", "user_text", "system_prompt", "answer", "completion",
    "retrieved", "context", "snippet", "sources", "docling", "text",
    "user_id", "userid", "student", "學號", "學生",
    "ip", "country", "user_agent", "useragent", "ua", "email",
)


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in _SENSITIVE_SUBSTRINGS)


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: (_REDACTED if _is_sensitive(str(k)) else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


# 結構 allowlist：只保留明確安全（非自由文字、非識別性）的頂層欄位；其餘整塊丟棄。
# 這些欄位由 Sentry 以伺服器端來源填充（hostname/參數化 route/套件版/SDK），本架構不寫入使用者內容。
# 前提（Opus L4）：route 為參數化名稱且**無 PII path 參數**（/chat /healthz /warmup /feedback）；
# 未來若新增帶 path 參數的 route，transaction 可能含使用者值→須改 transaction_style 或移出。
_ALLOWED_TOP_KEYS = frozenset({
    "event_id", "timestamp", "level", "platform", "logger",
    "sdk", "release", "environment", "server_name", "transaction", "modules",
})
# contexts 只留核准型別的核准子欄位（device 整個丟棄；trace.description 等自由文字去除）
_ALLOWED_CONTEXT_FIELDS = {
    "runtime": frozenset({"name", "version", "build"}),
    "os": frozenset({"name", "version", "build", "kernel_version"}),
    "trace": frozenset({"trace_id", "span_id", "parent_span_id", "op", "status"}),
}
# frame 只留程式碼定位（去 filename/abs_path/context_line/vars 等可能含路徑/自由文字者）
_ALLOWED_FRAME_KEYS = frozenset({"module", "function", "lineno", "in_app"})


def _safe_exc_value(v: dict) -> dict:
    """例外只保留 type 與 stacktrace 結構（frame 僅位置欄位）——去除 value/vars/context_line。"""
    out: dict = {}
    if isinstance(v.get("type"), str):
        out["type"] = v["type"]
    st = v.get("stacktrace")
    if isinstance(st, dict):
        out["stacktrace"] = {
            "frames": [
                {k: fr[k] for k in _ALLOWED_FRAME_KEYS if k in fr}
                for fr in (st.get("frames") or [])
                if isinstance(fr, dict)
            ]
        }
    return out


def scrub_event(event, hint):
    """Sentry before_send（**結構 allowlist**）：只保留核准頂層欄位，其餘（user/tags/extra/
    breadcrumbs/request/message/logentry/未知）整塊丟棄；例外僅留 type+stacktrace（frame 只留
    module/function/lineno/in_app）；contexts 僅留核准型別的核准子欄位（device 整個丟棄）並再
    key-scrub。非 dict/出錯→回 None 丟棄該 event。"""
    try:
        if not isinstance(event, dict):
            return None
        out = {k: event[k] for k in _ALLOWED_TOP_KEYS if k in event}
        exc = event.get("exception")
        if isinstance(exc, dict):
            out["exception"] = {
                "values": [
                    _safe_exc_value(v) for v in (exc.get("values") or []) if isinstance(v, dict)
                ]
            }
        ctx = event.get("contexts")
        if isinstance(ctx, dict):
            kept = {}
            for t, fields in _ALLOWED_CONTEXT_FIELDS.items():
                c = ctx.get(t)
                if isinstance(c, dict):
                    kept[t] = _scrub({k: c[k] for k in fields if k in c})
            out["contexts"] = kept
        return out
    except Exception:  # noqa: BLE001  寧可丟棄也不洩漏
        logger.warning("Sentry scrub_event 失敗→丟棄該 event", exc_info=True)
        return None


def init_sentry(settings) -> bool:
    """有 DSN 才 init（default-deny 脫敏）；無 DSN→no-op False；init 失敗→fail-open False。"""
    dsn = getattr(settings, "sentry_dsn", "")
    if not dsn:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            before_send=scrub_event,
            send_default_pii=False,
            max_request_body_size="never",
            include_local_variables=False,   # 不捕捉 frame 區域變數（防 query/user_text 洩漏）
            traces_sample_rate=0.0,          # 只收錯誤，trace 由 LangFuse 負責
        )
        return True
    except Exception:  # noqa: BLE001  init 失敗→fail-open，不擋啟動
        logger.warning("init_sentry 失敗→停用 Sentry 續行", exc_info=True)
        return False
