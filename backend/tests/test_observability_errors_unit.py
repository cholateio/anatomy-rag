"""Phase 9 Sentry default-deny 脫敏 + init（零外部呼叫；隱私硬紅線）。"""
from __future__ import annotations

from anatomy_backend.config import Settings
from anatomy_backend.observability.errors import init_sentry, scrub_event

_Q = "肱二頭肌的起止點是什麼"   # 代表 query/PHI 文字


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@localhost:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@localhost:5432/anatomy_rag",
        redis_url="redis://localhost:6379/0",
    )
    base.update(over)
    return Settings(**base)


def _has(obj, needle) -> bool:
    """遞迴搜尋值內是否含 needle 子字串。"""
    if isinstance(obj, str):
        return needle in obj
    if isinstance(obj, dict):
        return any(_has(v, needle) for v in obj.values())
    if isinstance(obj, list):
        return any(_has(v, needle) for v in obj)
    return False


def test_scrub_allowlist_drops_all_free_text_and_identifiers():
    ev = {
        "level": "error",
        "exception": {"values": [{"type": "ValueError", "value": f"invalid: {_Q}",
            "stacktrace": {"frames": [{"function": "f", "lineno": 1,
                                       "vars": {"query": _Q}, "context_line": _Q}]}}]},
        "message": f"failed {_Q}",
        "logentry": {"message": _Q},
        "breadcrumbs": {"values": [{"message": _Q, "data": {"q": _Q}}]},
        "request": {"data": {"query": _Q}, "query_string": f"q={_Q}"},
        "extra": {"whatever": _Q},
        "user": {"id": "B12345678", "username": _Q},
        "tags": {"note": _Q},
        "contexts": {"trace": {"trace_id": "abc", "span_id": "def",
                               "description": _Q, "user_id": "B12345678"},
                     "device": {"name": _Q},
                     "custom": {"note": _Q}},
    }
    out = scrub_event(ev, {})
    assert not _has(out, _Q)            # 任何自由文字皆不得殘留
    assert not _has(out, "B12345678")  # 原始識別碼亦不得殘留
    # 安全結構保留
    assert out["level"] == "error"
    assert out["exception"]["values"][0]["type"] == "ValueError"
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "f"
    # 自由文字/識別性容器整塊丟棄
    for dropped in ("user", "tags", "extra", "breadcrumbs", "request", "message", "logentry"):
        assert dropped not in out
    # contexts：core trace id 保留；自由文字/識別(user_id)/未核准型別(device/custom) 皆去除
    assert out["contexts"]["trace"]["trace_id"] == "abc"
    assert "description" not in out["contexts"]["trace"]
    assert "user_id" not in out["contexts"]["trace"]
    assert "device" not in out["contexts"]
    assert "custom" not in out["contexts"]


def test_scrub_returns_none_on_non_dict():
    assert scrub_event("not a dict", {}) is None


def test_scrub_returns_none_on_error():
    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")
    assert scrub_event(_Boom(), {}) is None


def test_init_sentry_noop_without_dsn():
    assert init_sentry(_settings(sentry_dsn="")) is False


def test_init_sentry_configures_privacy_options(monkeypatch):
    cap = {}
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: cap.update(kw))
    assert init_sentry(_settings(sentry_dsn="https://x@example.invalid/1")) is True
    assert cap["before_send"] is scrub_event
    assert cap["send_default_pii"] is False
    assert cap["max_request_body_size"] == "never"
    assert cap["include_local_variables"] is False


def test_init_sentry_fail_open_on_init_error(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("init boom")
    monkeypatch.setattr("sentry_sdk.init", _boom)
    assert init_sentry(_settings(sentry_dsn="https://x@example.invalid/1")) is False
