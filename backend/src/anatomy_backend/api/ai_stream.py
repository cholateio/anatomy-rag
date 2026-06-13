"""Vercel AI SDK UI Message Stream emitter（DL-018；無官方 Python lib 之核准例外）。

每幀 data: <compact-json>\n\n；自訂資料部件 type 前綴 data-（transient 不寫進 message.parts、
僅經 useChat onData）。標頭 x-vercel-ai-ui-message-stream: v1 必注入。研究確認 v5/v6 一致。
"""
from __future__ import annotations

import json

from sse_starlette import ServerSentEvent

# sse-starlette 已設 text/event-stream / no-cache / keep-alive / x-accel-buffering:no；
# 這裡只補 AI SDK marker（EventSourceResponse(headers=...) 注入）。
UI_MESSAGE_STREAM_HEADERS = {"x-vercel-ai-ui-message-stream": "v1"}


def start_part(message_id: str | None = None) -> dict:
    p: dict = {"type": "start"}
    if message_id is not None:
        p["messageId"] = message_id
    return p


def text_start_part(text_id: str) -> dict:
    return {"type": "text-start", "id": text_id}


def text_delta_part(text_id: str, delta: str) -> dict:
    return {"type": "text-delta", "id": text_id, "delta": delta}


def text_end_part(text_id: str) -> dict:
    return {"type": "text-end", "id": text_id}


def finish_part() -> dict:
    return {"type": "finish"}


def data_part(name: str, data, *, transient: bool = True) -> dict:
    """自訂資料部件：type=data-<name>、payload 欄位 data、transient 預設 True。"""
    p = {"type": f"data-{name}", "data": data}
    if transient:
        p["transient"] = True
    return p


def _compact(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def sse_event(part: dict) -> ServerSentEvent:
    """part → ServerSentEvent（data=compact JSON、sep="\\n"、無 event name）。"""
    return ServerSentEvent(data=_compact(part), sep="\n")


def done_event() -> ServerSentEvent:
    return ServerSentEvent(data="[DONE]", sep="\n")
