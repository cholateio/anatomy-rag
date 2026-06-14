"""Phase 9 tracing 單元測試（NoOp/Langfuse/build_tracer；零外部呼叫）。"""
from __future__ import annotations

import pytest

from anatomy_backend.observability.tracing import NoOpTracer


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
