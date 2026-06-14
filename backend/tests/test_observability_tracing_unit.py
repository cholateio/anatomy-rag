"""Phase 9 tracing 單元測試（NoOp/Langfuse/build_tracer；零外部呼叫）。"""
from __future__ import annotations

import pytest
from contextlib import contextmanager

from anatomy_backend.config import Settings
from anatomy_backend.observability.tracing import (
    LangfuseTracer,
    NoOpTracer,
    _pseudonymize,
    build_tracer,
)


async def test_noop_trace_and_span_do_not_change_returns():
    t = NoOpTracer()
    with t.trace("chat", user_id="u1", metadata={"k": "v"}):
        with t.span("encode"):
            result = 42
    assert result == 42


def test_noop_score_and_flush_are_safe_noops():
    t = NoOpTracer()
    t.score("cache_hit", 1.0)
    t.flush()


async def test_noop_does_not_swallow_body_exception():
    t = NoOpTracer()
    with pytest.raises(ValueError):
        with t.trace("chat"):
            raise ValueError("body")


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@localhost:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@localhost:5432/anatomy_rag",
        redis_url="redis://localhost:6379/0",
    )
    base.update(over)
    return Settings(**base)


class _FakeLangfuse:
    def __init__(self, *, fail_exit=False, fail_score=False):
        self.attrs = []
        self.spans = []
        self.scores = []
        self.flushed = 0
        self._fail_exit = fail_exit
        self._fail_score = fail_score

    @contextmanager
    def propagate_attributes(self, **kw):
        self.attrs.append(kw)
        yield

    @contextmanager
    def start_as_current_observation(self, *, name):
        self.spans.append(name)
        yield object()
        if self._fail_exit:
            raise RuntimeError("exit boom")

    def score_current_span(self, *, name, value, comment=None):
        if self._fail_score:
            raise RuntimeError("score boom")
        self.scores.append((name, value))

    def flush(self):
        self.flushed += 1


def test_pseudonymize_hmac_stable_not_raw_requires_salt():
    p = _pseudonymize("user-123", salt="high-entropy-salt")
    assert p and p != "user-123" and len(p) == 32          # 128 bits hex
    assert p == _pseudonymize("user-123", salt="high-entropy-salt")    # 穩定
    assert p != _pseudonymize("user-123", salt="other-salt")           # salt 影響
    assert _pseudonymize("user-123", salt="") is None      # 空 salt→None（不可反查）
    assert _pseudonymize(None, salt="s") is None


def test_build_tracer_noop_when_unconfigured():
    assert isinstance(build_tracer(_settings()), NoOpTracer)


def test_build_tracer_langfuse_when_configured(monkeypatch):
    class _FakeLF:
        def __init__(self, **kw):
            pass
    monkeypatch.setattr("langfuse.Langfuse", _FakeLF)
    t = build_tracer(_settings(
        langfuse_host="http://lf:3000", langfuse_public_key="pk", langfuse_secret_key="sk",
        langfuse_user_id_salt="high-entropy-salt"))
    assert isinstance(t, LangfuseTracer)


def test_build_tracer_noop_when_salt_missing(monkeypatch):
    # 有 LangFuse 金鑰但無 salt→拒啟用（NoOp），防低熵 ID 假名反查（Codex#2 v2）
    monkeypatch.setattr("langfuse.Langfuse", lambda **kw: object())
    t = build_tracer(_settings(
        langfuse_host="http://lf:3000", langfuse_public_key="pk", langfuse_secret_key="sk"))
    assert isinstance(t, NoOpTracer)


def test_build_tracer_fail_open_when_construction_raises(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("construct boom")
    monkeypatch.setattr("langfuse.Langfuse", _boom)
    t = build_tracer(_settings(
        langfuse_host="http://lf:3000", langfuse_public_key="pk", langfuse_secret_key="sk",
        langfuse_user_id_salt="s"))
    assert isinstance(t, NoOpTracer)   # 建構失敗→fail-open NoOp，不擋啟動


async def test_langfuse_tracer_pseudonymous_user_id_and_metadata_allowlist():
    fake = _FakeLangfuse()
    t = LangfuseTracer(fake, id_salt="high-entropy-salt")
    with t.trace("chat", user_id="raw-user-9",
                 metadata={"is_followup": False, "query": "肱二頭肌", "user_id": "raw-user-9"}):
        pass
    attrs = fake.attrs[0]
    assert attrs["user_id"] == _pseudonymize("raw-user-9", "high-entropy-salt")
    assert attrs["user_id"] != "raw-user-9"
    md = attrs["metadata"]
    assert md.get("is_followup") is False
    assert "query" not in md and "user_id" not in md   # metadata allowlist 擋掉自由文字/原始 id


async def test_langfuse_tracer_span_and_score_delegate():
    fake = _FakeLangfuse()
    t = LangfuseTracer(fake)
    with t.span("encode"):
        r = 5
    t.score("cache_hit", 1.0)
    t.flush()
    assert r == 5 and "encode" in fake.spans and ("cache_hit", 1.0) in fake.scores
    assert fake.flushed == 1


async def test_langfuse_tracer_exit_failure_is_fail_open():
    # span 結束時 langfuse __exit__ 拋→不得中斷業務、不得 RuntimeError
    t = LangfuseTracer(_FakeLangfuse(fail_exit=True))
    with t.span("encode"):
        r = 1
    assert r == 1


async def test_langfuse_tracer_does_not_swallow_body_exception():
    t = LangfuseTracer(_FakeLangfuse())
    with pytest.raises(ValueError):
        with t.trace("chat", user_id="u"):
            raise ValueError("body")


async def test_langfuse_tracer_score_fail_open():
    LangfuseTracer(_FakeLangfuse(fail_score=True)).score("x", 1.0)  # 不拋


async def test_safe_cm_partial_enter_then_rollback_failure_does_not_escape():
    # Codex#3 v2：第一個 CM enter OK 但 exit 拋；第二個 CM enter 拋→回滾第一個 exit 又拋。
    # _safe_cm 不得讓任何 tracer 例外逃出（業務照常執行）。
    from anatomy_backend.observability.tracing import _safe_cm

    @contextmanager
    def _ok_enter_bad_exit():
        yield
        raise RuntimeError("exit boom")

    def _bad_enter():
        raise RuntimeError("enter boom")

    with _safe_cm([lambda: _ok_enter_bad_exit(), _bad_enter]):
        r = 99
    assert r == 99


async def test_safe_cm_forwards_body_exception_to_span_exit():
    # Codex#medium v3：業務例外須轉發給 span __exit__（標記 error），且原始例外照常傳播。
    from anatomy_backend.observability.tracing import _safe_cm

    seen = {}

    class _RecCM:
        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            seen["exc_type"] = et
            return False   # 不抑制

    with pytest.raises(ValueError):
        with _safe_cm([_RecCM]):
            raise ValueError("body")
    assert seen["exc_type"] is ValueError


async def test_langfuse_metadata_value_validation_and_score_allowlist():
    fake = _FakeLangfuse()
    t = LangfuseTracer(fake, id_salt="s")
    long_text = "這是一段很長的查詢文字應被擋下" * 2   # >16 字
    with t.trace("chat", user_id="u",
                 metadata={"status": long_text, "is_followup": True, "kb_version": 1}):
        pass
    md = fake.attrs[0]["metadata"]
    assert md == {"is_followup": True, "kb_version": 1}   # 長字串 value 被擋；只留安全 primitive
    t.score("cache_hit", 1.0)                              # allowlist name→送
    t.score("evil_name", 1.0, comment="leak query")       # 非 allowlist→丟棄、comment 不外送
    assert ("cache_hit", 1.0) in fake.scores
    assert all(n != "evil_name" for n, _ in fake.scores)
