"""Phase 9 告警條件 + notifier（邏輯+介面；operational 延後）。"""
from __future__ import annotations

from anatomy_backend.observability.alerts import LogNotifier, evaluate_alerts


def test_p95_latency_breach_triggers_must():
    a = evaluate_alerts({"p95_latency_s": 9.0, "p95_breach_minutes": 10})
    assert any(x.name == "p95_latency" and x.severity == "must" for x in a)


def test_p95_below_threshold_or_short_no_trigger():
    assert evaluate_alerts({"p95_latency_s": 9.0, "p95_breach_minutes": 9}) == []
    assert evaluate_alerts({"p95_latency_s": 7.9, "p95_breach_minutes": 30}) == []


def test_model_error_rate_triggers():
    assert "model_error_rate" in {x.name for x in
        evaluate_alerts({"model_error_rate": 0.06, "model_error_minutes": 5})}


def test_usage_ratio_triggers_at_80pct():
    assert "usage_ratio" in {x.name for x in evaluate_alerts({"usage_ratio": 0.80})}
    assert evaluate_alerts({"usage_ratio": 0.79}) == []


def test_citation_fail_is_should_severity():
    a = evaluate_alerts({"citation_fail_rate": 0.11, "citation_fail_minutes": 30})
    assert any(x.name == "citation_fail_rate" and x.severity == "should" for x in a)


def test_empty_metrics_no_alerts():
    assert evaluate_alerts({}) == []


def test_log_notifier_does_not_raise():
    LogNotifier().notify(evaluate_alerts({"usage_ratio": 0.9})[0])
