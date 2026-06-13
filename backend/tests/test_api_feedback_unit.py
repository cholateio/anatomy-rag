"""feedback 單元測試（§6.5 / DL-022）。

零 DB 呼叫——writer 注入假 async callable。
路由 400 驗證：httpx.ASGITransport（不啟動 lifespan）。
"""
import uuid

import httpx
import pytest
from anatomy_backend.api.feedback import FeedbackInput, apply_feedback, parse_feedback_body


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


# ── parse_feedback_body 驗證（Fix 3）────────────────────────────────────────


def test_parse_feedback_body_bad_uuid_raises():
    """conversation_id 不是合法 UUID → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"conversation_id": "not-a-uuid", "rating": 1})


def test_parse_feedback_body_missing_conversation_id_raises():
    """conversation_id 缺失 → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"rating": 1})


def test_parse_feedback_body_non_numeric_rating_raises():
    """rating 不可轉 int → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"conversation_id": str(uuid.uuid4()), "rating": "bad"})


def test_parse_feedback_body_missing_rating_raises():
    """rating 缺失 → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"conversation_id": str(uuid.uuid4())})


def test_parse_feedback_body_invalid_rating_value_raises():
    """rating=0 通過 int() 但 FeedbackInput.__post_init__ 拒絕 → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"conversation_id": str(uuid.uuid4()), "rating": 0})


def test_parse_feedback_body_valid():
    """合法輸入正常解析。"""
    cid = str(uuid.uuid4())
    fb = parse_feedback_body({"conversation_id": cid, "rating": 1, "text": "good"})
    assert fb.conversation_id == cid
    assert fb.rating == 1
    assert fb.text == "good"


# ── 路由層 400 驗證（Fix 3）─────────────────────────────────────────────────


async def test_feedback_route_non_numeric_rating_returns_400():
    """路由：rating='bad' → HTTP 400（非 500）。"""
    from anatomy_backend.api.auth import User, get_current_user
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    async def _noop(**kw):
        pass

    app.state.write_feedback = _noop
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post(
                "/feedback",
                json={"conversation_id": str(uuid.uuid4()), "rating": "bad"},
            )
        assert resp.status_code == 400
    finally:
        del app.dependency_overrides[get_current_user]
        try:
            delattr(app.state, "write_feedback")
        except AttributeError:
            pass


async def test_feedback_route_bad_uuid_returns_400():
    """路由：conversation_id 不是 UUID → HTTP 400。"""
    from anatomy_backend.api.auth import User, get_current_user
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    async def _noop(**kw):
        pass

    app.state.write_feedback = _noop
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post(
                "/feedback",
                json={"conversation_id": "not-a-uuid", "rating": 1},
            )
        assert resp.status_code == 400
    finally:
        del app.dependency_overrides[get_current_user]
        try:
            delattr(app.state, "write_feedback")
        except AttributeError:
            pass
