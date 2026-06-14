"""Tests for OutOfScopeCorrectness pure-Python metric (Task 2.1).

All tests marked @pytest.mark.ragas — requires the ``ragas`` extra.
"""
import pytest
from anatomy_eval.ragas_metrics import OOS_PHRASE, OutOfScopeCorrectness  # noqa: E402
from ragas.dataset_schema import SingleTurnSample  # type: ignore[import]  # noqa: E402

# ── helpers ────────────────────────────────────────────────────────────────────


def _score(response: str, reference: str) -> float:
    """Synchronously compute OutOfScopeCorrectness for a sample."""
    import asyncio

    metric = OutOfScopeCorrectness()
    sample = SingleTurnSample(user_input="q", response=response, reference=reference)
    # Use asyncio.run() to create a fresh event loop — avoids failures when
    # pytest-asyncio has already closed the global loop in earlier tests.
    return asyncio.run(metric._single_turn_ascore(sample, callbacks=None))


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.ragas
def test_oos_correctness_both_phrase():
    """Both response and reference contain OOS phrase → 1.0 (both agree: OOS)."""
    response = f"根據教材，{OOS_PHRASE}。"
    reference = OOS_PHRASE
    assert _score(response, reference) == 1.0


@pytest.mark.ragas
def test_oos_correctness_fabricated_oos():
    """System says OOS but reference expects in-scope content → 0.0 (false OOS)."""
    response = OOS_PHRASE  # system incorrectly claims OOS
    reference = "肱二頭肌起點為喙突 biceps brachii coracoid process"
    assert _score(response, reference) == 0.0


@pytest.mark.ragas
def test_oos_correctness_in_scope():
    """Neither response nor reference contains OOS phrase → 1.0 (both agree: in-scope)."""
    response = "肱二頭肌起點為喙突和肩胛骨盂上結節。"
    reference = "biceps brachii coracoid process radial tuberosity"
    assert _score(response, reference) == 1.0


@pytest.mark.ragas
def test_oos_correctness_missed_oos():
    """Reference is OOS but response fabricates an answer → 0.0 (missed OOS)."""
    response = "克氏循環的酵素調控包含多種輔酶。"
    reference = OOS_PHRASE  # expected to say OOS
    assert _score(response, reference) == 0.0


@pytest.mark.ragas
def test_oos_metric_name():
    metric = OutOfScopeCorrectness()
    assert metric.name == "out_of_scope_correctness"


@pytest.mark.ragas
def test_oos_metric_required_columns():
    """Metric declares it needs response and reference columns."""
    from ragas.metrics.base import MetricType

    metric = OutOfScopeCorrectness()
    cols = metric._required_columns.get(MetricType.SINGLE_TURN, set())
    assert "response" in cols
    assert "reference" in cols


@pytest.mark.ragas
def test_oos_metric_no_llm_required():
    """OutOfScopeCorrectness must NOT be a MetricWithLLM."""
    from ragas.metrics.base import MetricWithLLM

    assert not isinstance(OutOfScopeCorrectness(), MetricWithLLM)
