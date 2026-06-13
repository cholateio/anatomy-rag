"""使用者回饋（§6.5）：👍/👎 + 文字 → query_logs.feedback / feedback_text（DL-022）。

rating ∈ {1,-1}；text 應用層截斷 ≤2000；MUST 經 auth（user_id 由 get_current_user 提供）。
高頻拒絕事件不逐筆寫 DB（DL-022：Redis TTL 計數）；回饋寫入不在此限（低頻、有價值）。

DB schema（005_query_logs.py）：
    feedback     SMALLINT CHECK (feedback IN (-1, 0, 1))
    feedback_text TEXT
    conversation_id UUID
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request

from anatomy_backend.api.auth import User, get_current_user

router = APIRouter()
_TEXT_MAX = 2000


@dataclass(frozen=True)
class FeedbackInput:
    conversation_id: str
    rating: int
    text: str | None

    def __post_init__(self) -> None:
        if self.rating not in (1, -1):
            raise ValueError("rating 必須為 1 或 -1（1=👍 / -1=👎）")


async def apply_feedback(
    fb: FeedbackInput,
    *,
    user_id: str,
    writer: Callable[..., Awaitable[None]],
) -> None:
    """驗後寫入。writer 為注入式 async callable（生產用 DB write；測試用 fake）。"""
    text = fb.text[:_TEXT_MAX] if fb.text is not None else None
    await writer(
        user_id=user_id,
        conversation_id=fb.conversation_id,
        rating=fb.rating,
        text=text,
    )


@router.post("/feedback")
async def feedback(
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """接收前端 👍/👎 回饋，寫入 query_logs.feedback / feedback_text。"""
    body = await request.json()
    fb = FeedbackInput(
        conversation_id=body.get("conversation_id"),
        rating=int(body.get("rating", 0)),
        text=body.get("text"),
    )
    await apply_feedback(
        fb, user_id=user.user_id, writer=request.app.state.write_feedback
    )
    return {"ok": True}
