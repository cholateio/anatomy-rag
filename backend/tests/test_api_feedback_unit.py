"""feedback 單元測試（§6.5 / DL-022 + DL-027 turn_id）。

零 DB 呼叫——writer 注入假 async callable。
路由 400/404 驗證：httpx.ASGITransport（不啟動 lifespan）。
"""
import uuid

import httpx
import pytest
from anatomy_backend.api.feedback import FeedbackInput, apply_feedback, parse_feedback_body

FIXED_TURN = "00000000-0000-0000-0000-0000000000aa"


async def test_thumbs_down_with_text_written():
    rec: dict = {}

    async def _writer(**kw) -> bool:
        rec.update(kw)
        return True

    await apply_feedback(
        FeedbackInput(message_id=FIXED_TURN, rating=-1, text="頁碼錯誤"),
        user_id="u1",
        writer=_writer,
    )
    assert rec["rating"] == -1 and rec["text"] == "頁碼錯誤" and rec["user_id"] == "u1"
    assert rec["message_id"] == FIXED_TURN


async def test_rating_must_be_plus_minus_one():
    with pytest.raises(ValueError):
        FeedbackInput(message_id=FIXED_TURN, rating=0, text=None)


async def test_thumbs_up_written():
    rec: dict = {}

    async def _writer(**kw) -> bool:
        rec.update(kw)
        return True

    await apply_feedback(
        FeedbackInput(message_id=FIXED_TURN, rating=1, text=None),
        user_id="u2",
        writer=_writer,
    )
    assert rec["rating"] == 1 and rec["user_id"] == "u2"


async def test_text_truncated_to_limit():
    rec: dict = {}

    async def _writer(**kw) -> bool:
        rec.update(kw)
        return True

    await apply_feedback(
        FeedbackInput(message_id=FIXED_TURN, rating=1, text="x" * 5000),
        user_id="u1",
        writer=_writer,
    )
    assert len(rec["text"]) <= 2000


async def test_none_text_passed_through():
    rec: dict = {}

    async def _writer(**kw) -> bool:
        rec.update(kw)
        return True

    await apply_feedback(
        FeedbackInput(message_id=FIXED_TURN, rating=1, text=None),
        user_id="u1",
        writer=_writer,
    )
    assert rec["text"] is None


# ── parse_feedback_body 驗證 ─────────────────────────────────────────────────


def test_parse_feedback_body_bad_uuid_raises():
    """message_id 不是合法 UUID → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": "not-a-uuid", "rating": 1})


def test_parse_feedback_body_missing_message_id_raises():
    """message_id 缺失 → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"rating": 1})


def test_parse_feedback_body_non_numeric_rating_raises():
    """rating 不可轉 int → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": str(uuid.uuid4()), "rating": "bad"})


def test_parse_feedback_body_missing_rating_raises():
    """rating 缺失 → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": str(uuid.uuid4())})


def test_parse_feedback_body_invalid_rating_value_raises():
    """rating=0 通過 int() 但 FeedbackInput.__post_init__ 拒絕 → ValueError。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": str(uuid.uuid4()), "rating": 0})


def test_parse_feedback_body_valid():
    """合法輸入正常解析。"""
    mid = str(uuid.uuid4())
    fb = parse_feedback_body({"message_id": mid, "rating": 1, "text": "good"})
    assert fb.message_id == mid
    assert fb.rating == 1
    assert fb.text == "good"


# ── 路由層 400 驗證 ─────────────────────────────────────────────────────────


async def test_feedback_route_non_numeric_rating_returns_400():
    """路由：rating='bad' → HTTP 400（非 500）。"""
    from anatomy_backend.api.auth import User, get_current_user
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    async def _noop(**kw) -> bool:
        return True

    app.state.write_feedback = _noop
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post(
                "/feedback",
                json={"message_id": str(uuid.uuid4()), "rating": "bad"},
            )
        assert resp.status_code == 400
    finally:
        del app.dependency_overrides[get_current_user]
        try:
            delattr(app.state, "write_feedback")
        except AttributeError:
            pass


async def test_feedback_route_bad_uuid_returns_400():
    """路由：message_id 不是 UUID → HTTP 400。"""
    from anatomy_backend.api.auth import User, get_current_user
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    async def _noop(**kw) -> bool:
        return True

    app.state.write_feedback = _noop
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post(
                "/feedback",
                json={"message_id": "not-a-uuid", "rating": 1},
            )
        assert resp.status_code == 400
    finally:
        del app.dependency_overrides[get_current_user]
        try:
            delattr(app.state, "write_feedback")
        except AttributeError:
            pass


def test_parse_feedback_non_string_text_raises():
    # Codex 終審 P2c：非字串 text → ValueError（路由轉 400），不得滑到 500
    cid = str(uuid.uuid4())
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": cid, "rating": 1, "text": 123})


# ── M2：空字串 / 純空白 text → ValueError（路由轉 400）────────────────────────


def test_parse_feedback_body_empty_text_raises():
    """text 為空字串 → ValueError（M2：空值不可接受）。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": str(uuid.uuid4()), "rating": 1, "text": ""})


def test_parse_feedback_body_whitespace_text_raises():
    """text 為純空白 → ValueError（M2）。"""
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": str(uuid.uuid4()), "rating": 1, "text": "   "})


# ── M1：writer 拋 DB 例外 → 路由 HTTP 500 ───────────────────────────────────


async def test_feedback_route_writer_exception_returns_500():
    """writer 拋 DB 例外 → HTTP 500（不被吞成 404，M1）。"""
    from anatomy_backend.api.auth import User, get_current_user
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    async def _boom(**kw) -> bool:
        raise RuntimeError("DB connection lost")

    app.state.write_feedback = _boom
    try:
        # raise_app_exceptions=False：讓 Starlette ServerErrorMiddleware 將未捕獲例外
        # 轉為 HTTP 500，而非在 httpx 層重拋（預設 raise_app_exceptions=True）。
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post(
                "/feedback",
                json={"message_id": FIXED_TURN, "rating": 1},
            )
        assert resp.status_code == 500
    finally:
        del app.dependency_overrides[get_current_user]
        try:
            delattr(app.state, "write_feedback")
        except AttributeError:
            pass


# ── DL-027：writer 回 False → 路由 404 ─────────────────────────────────────


async def test_feedback_route_returns_404_when_writer_returns_false():
    """writer 回傳 False（turn_id 不存在或 user 不符）→ HTTP 404。"""
    from anatomy_backend.api.auth import User, get_current_user
    from anatomy_backend.api.main import app

    app.dependency_overrides[get_current_user] = lambda: User("u1", False)

    async def _not_found(**kw) -> bool:
        return False

    app.state.write_feedback = _not_found
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post(
                "/feedback",
                json={"message_id": FIXED_TURN, "rating": 1},
            )
        assert resp.status_code == 404
    finally:
        del app.dependency_overrides[get_current_user]
        try:
            delattr(app.state, "write_feedback")
        except AttributeError:
            pass
