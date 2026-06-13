"""feedback 單元測試（§6.5 / DL-022）。

零 DB 呼叫——writer 注入假 async callable。
"""
import pytest

from anatomy_backend.api.feedback import FeedbackInput, apply_feedback


async def test_thumbs_down_with_text_written():
    rec: dict = {}

    async def _writer(**kw):
        rec.update(kw)

    await apply_feedback(
        FeedbackInput(conversation_id="c1", rating=-1, text="頁碼錯誤"),
        user_id="u1",
        writer=_writer,
    )
    assert rec["rating"] == -1 and rec["text"] == "頁碼錯誤" and rec["user_id"] == "u1"


async def test_rating_must_be_plus_minus_one():
    with pytest.raises(ValueError):
        FeedbackInput(conversation_id="c1", rating=0, text=None)


async def test_thumbs_up_written():
    rec: dict = {}

    async def _writer(**kw):
        rec.update(kw)

    await apply_feedback(
        FeedbackInput(conversation_id="c1", rating=1, text=None),
        user_id="u2",
        writer=_writer,
    )
    assert rec["rating"] == 1 and rec["user_id"] == "u2"


async def test_text_truncated_to_limit():
    rec: dict = {}

    async def _writer(**kw):
        rec.update(kw)

    await apply_feedback(
        FeedbackInput(conversation_id="c1", rating=1, text="x" * 5000),
        user_id="u1",
        writer=_writer,
    )
    assert len(rec["text"]) <= 2000


async def test_none_text_passed_through():
    rec: dict = {}

    async def _writer(**kw):
        rec.update(kw)

    await apply_feedback(
        FeedbackInput(conversation_id="c1", rating=1, text=None),
        user_id="u1",
        writer=_writer,
    )
    assert rec["text"] is None
