"""請求正規化（§5.7 / §5.9 DL-021）+ PageCitation。

後端 MUST 只讀最後兩則 user 訊息（當前＋前一問）；其餘歷史 MUST NOT 進入任何 LLM payload。
追問判定為純規則（零 LLM 成本）：含中英指代詞或長度 < 8 字、且存在前一問。

F6/M 輸入驗證（§5.7）：
  - 1 <= len(query) <= 2000，違反 raise ValueError
  - conversation_id 若有須合法 UUID，違反 raise ValueError
  - metadata_filter 為 dict | None，違反 raise ValueError
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import BaseModel

# DL-021 §5.9：追問指代詞（中英）
_PRONOUNS = ("它", "其", "這", "那", "該", "this", "it", "that", "those", "these")
_FOLLOWUP_LEN = 8
_MAX_QUERY_LEN = 2000


@dataclass(frozen=True)
class NormalizedChat:
    query: str
    prev_query: str | None
    metadata_filter: dict | None
    conversation_id: str | None
    is_followup: bool


class PageCitation(BaseModel):
    book_title: str
    edition: str | None = None
    page: int
    figure: str | None = None
    image_url: str
    snippet: str
    score: float


def _text_of(msg: dict) -> str:
    """支援 parts（[{type:text,text}]）與 content 字串兩種 useChat 形狀。"""
    parts = msg.get("parts")
    if isinstance(parts, list):
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    content = msg.get("content")
    return content if isinstance(content, str) else ""


def _is_followup(query: str, prev_query: str | None) -> bool:
    if prev_query is None:
        return False
    if len(query.strip()) < _FOLLOWUP_LEN:
        return True
    # 英文指代詞大小寫不敏感；中文不受 lower() 影響（規則 OPEN，Phase 11 調校）
    return any(p in query.lower() for p in _PRONOUNS)


def normalize_chat(body: dict) -> NormalizedChat:
    """正規化 useChat 請求並驗證輸入（F6/M §5.7）。"""
    if not isinstance(body, dict):
        raise ValueError("請求 body 必須為物件")
    messages = body.get("messages") or []
    user_texts = [_text_of(m) for m in messages if m.get("role") == "user"]
    user_texts = [t for t in user_texts if t]
    if not user_texts:
        raise ValueError("請求無任何 user 訊息")

    query = user_texts[-1]
    prev_query = user_texts[-2] if len(user_texts) >= 2 else None

    # F6/M: query 長度驗證
    if not (1 <= len(query) <= _MAX_QUERY_LEN):
        raise ValueError(f"query 長度須介於 1～{_MAX_QUERY_LEN}，收到 {len(query)}")

    # F6/M: metadata_filter 型別驗證
    metadata_filter = body.get("metadata_filter")
    if metadata_filter is not None and not isinstance(metadata_filter, dict):
        raise ValueError(
            f"metadata_filter 須為 dict 或 None，收到 {type(metadata_filter).__name__}"
        )

    # F6/M: conversation_id UUID 驗證
    conversation_id: str | None = body.get("conversation_id")
    if conversation_id is not None:
        try:
            uuid.UUID(conversation_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"conversation_id 須為合法 UUID，收到 {conversation_id!r}") from exc

    return NormalizedChat(
        query=query,
        prev_query=prev_query,
        metadata_filter=metadata_filter,
        conversation_id=conversation_id,
        is_followup=_is_followup(query, prev_query),
    )
