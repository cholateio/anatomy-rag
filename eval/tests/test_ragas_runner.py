"""Tests for ragas_runner (Task 2.2).

Covers:
- test_deterministic_core_offline: no LLM, no network, all scores finite.
- test_run_eval_never_calls_openai: OpenAI patched to raise — verify not called.
- test_llm_wiring_runs_without_network: FakeLLM/FakeEmb, tolerates NaN.
- test_run_eval_raises_if_llm_metric_without_llm: ValueError guard.
- test_build_rows_from_golden: mapping logic.

All marked @pytest.mark.ragas.
"""
import math

import pytest
from anatomy_eval.ragas_runner import (
    EvalRow,
    build_rows_from_golden,
    deterministic_metrics,
    llm_metrics,
    run_eval,
)

# ── fixtures / helpers ─────────────────────────────────────────────────────────


def _sample_rows() -> list:
    """Minimal two-row dataset covering OOS and non-OOS cases."""
    return [
        # Non-OOS: neither response nor reference contains OOS phrase.
        EvalRow(
            query="肱二頭肌的起止點是什麼？",
            retrieved_contexts=["biceps brachii origin coracoid process supraglenoid tubercle"],
            answer="肱二頭肌起於喙突和肩胛骨，止於橈骨粗隆。",
            reference="biceps brachii coracoid process radial tuberosity",
            is_oos=False,
        ),
        # OOS: both response and reference contain the OOS phrase.
        EvalRow(
            query="今天台北的天氣如何？",
            retrieved_contexts=["教材中查無此項"],
            answer="教材中查無此項",
            reference="教材中查無此項",
            is_oos=True,
        ),
    ]


# ── deterministic core ────────────────────────────────────────────────────────


@pytest.mark.ragas
def test_deterministic_core_offline():
    """Deterministic metrics (no LLM, no network) produce finite scores.

    This is the primary correctness assertion.  We also verify that
    out_of_scope_correctness is 1.0 for both sample rows (both agree on OOS
    presence/absence).
    """
    rows = _sample_rows()
    result = run_eval(rows, metrics=deterministic_metrics())

    assert isinstance(result, dict), "run_eval must return a dict"
    assert len(result) > 0, "result must have at least one metric"

    for name, score in result.items():
        assert math.isfinite(score), f"Score for {name!r} is not finite: {score}"

    # out_of_scope_correctness should be 1.0: both rows agree on OOS status.
    assert "out_of_scope_correctness" in result
    assert result["out_of_scope_correctness"] == pytest.approx(1.0)


@pytest.mark.ragas
def test_run_eval_never_calls_openai(monkeypatch):
    """OpenAI constructors AND outbound HTTP patched to raise — must NOT trigger them.

    This is the zero-cost / zero-network guarantee test [C-1].
    Clears OPENAI_API_KEY so no accidental env var is picked up.

    Three guards are applied:
    1. openai.OpenAI / openai.AsyncOpenAI constructors → raise (zero-LLM guarantee).
    2. requests.post → raise (RAGAS analytics guard [C-1]: proves RAGAS_DO_NOT_TRACK
       prevents the flush to https://t.explodinggradients.com).
    3. requests.Session.request → raise (belt-and-suspenders underlying HTTP guard).

    If any outbound HTTP or OpenAI call escapes, the patched function raises
    AssertionError and the test fails — proving the telemetry fix works.
    """
    import requests

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _openai_sentinel = AssertionError(
        "openai.OpenAI / openai.AsyncOpenAI must never be called in deterministic run"
    )
    _network_sentinel = AssertionError(
        "requests.post / Session.request must never be called in deterministic run [C-1]: "
        "check RAGAS_DO_NOT_TRACK is set before ragas import in ragas_runner.py"
    )

    def _raise_openai(*a, **kw):
        raise _openai_sentinel

    def _raise_network(*a, **kw):
        raise _network_sentinel

    monkeypatch.setattr("openai.OpenAI", _raise_openai)
    monkeypatch.setattr("openai.AsyncOpenAI", _raise_openai)
    monkeypatch.setattr(requests, "post", _raise_network)
    monkeypatch.setattr(requests.Session, "request", _raise_network)

    rows = _sample_rows()
    # Must complete without raising either sentinel.
    result = run_eval(rows, metrics=deterministic_metrics())

    for name, score in result.items():
        assert math.isfinite(score), f"{name}: {score} is not finite"


# ── LLM-wiring thin test ──────────────────────────────────────────────────────


@pytest.mark.ragas
def test_llm_wiring_runs_without_network(monkeypatch):
    """FakeLLM + FakeEmb: run_eval returns a dict without any network calls.

    This is a thin wiring test only.  NaN scores are acceptable — we do NOT
    assert exact values [H-3].  We assert that openai.OpenAI was never called.
    """
    from _ragas_fakes import FakeRagasEmbeddings, FakeRagasLLM

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _raise(*a, **kw):
        raise AssertionError("openai.OpenAI must not be called in LLM-wiring test")

    monkeypatch.setattr("openai.OpenAI", _raise)
    monkeypatch.setattr("openai.AsyncOpenAI", _raise)

    rows = _sample_rows()
    result = run_eval(
        rows,
        metrics=llm_metrics(),
        llm=FakeRagasLLM(),
        embeddings=FakeRagasEmbeddings(),
        raise_exceptions=False,  # tolerant path: NaN acceptable [plan §2.2]
    )

    assert isinstance(result, dict), "run_eval must return a dict even with fake LLM"
    # NaN is acceptable — do NOT assert math.isfinite for LLM wiring.


# ── ValueError guard ──────────────────────────────────────────────────────────


@pytest.mark.ragas
def test_run_eval_raises_if_llm_metric_without_llm():
    """run_eval must raise ValueError when LLM metrics are used without llm=.

    This prevents ragas from falling back to openai.OpenAI() automatically.
    """
    rows = _sample_rows()
    with pytest.raises(ValueError, match="llm=None"):
        run_eval(rows, metrics=llm_metrics(), llm=None)


# ── build_rows_from_golden ────────────────────────────────────────────────────


@pytest.mark.ragas
def test_build_rows_from_golden():
    """build_rows_from_golden maps GoldenQA to EvalRow correctly."""
    from anatomy_eval.golden import GoldenQA
    from anatomy_eval.ragas_metrics import OOS_PHRASE

    golden = [
        GoldenQA(
            id="t1",
            category="text_only",
            query="What is anatomy?",
            expected_pages=("gray42:1",),
            expected_concepts=("anatomy", "body structures"),
        ),
        GoldenQA(
            id="t2",
            category="out_of_scope",
            query="今天台北天氣？",
            expected_pages=(),
            expected_response_type="教材中查無此項",
        ),
    ]

    def _provider(qa):
        if qa.category == "out_of_scope":
            return OOS_PHRASE, [OOS_PHRASE]
        return "test answer", ["context for " + qa.query]

    rows = build_rows_from_golden(golden, _provider)

    assert len(rows) == 2

    r0 = rows[0]
    assert r0.query == "What is anatomy?"
    assert r0.is_oos is False
    assert r0.reference == "anatomy body structures"  # expected_concepts joined
    assert r0.answer == "test answer"
    assert "context for What is anatomy?" in r0.retrieved_contexts

    r1 = rows[1]
    assert r1.is_oos is True
    assert r1.reference == OOS_PHRASE
    assert r1.answer == OOS_PHRASE


@pytest.mark.ragas
def test_llm_metrics_names_match_yaml_active_keys():
    """Contract test: llm_metrics() names must exactly match eval_thresholds.yaml active keys.

    [C-2] gate.check_ragas iterates ``ragas.active`` keys and calls
    ``report.get(key)``; a missing key is treated as a fail.
    LLMContextPrecisionWithoutReference defaults to name
    ``llm_context_precision_without_reference`` but the YAML key is
    ``context_precision`` — we override .name in llm_metrics().

    This test ties the runner output to the gate config so a mismatch is
    caught immediately rather than silently failing the quality gate at eval time.
    """
    metrics = llm_metrics()
    names = {m.name for m in metrics}
    yaml_active_keys = {
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "out_of_scope_correctness",
    }
    assert names == yaml_active_keys, (
        f"llm_metrics() names {sorted(names)!r} != "
        f"YAML active keys {sorted(yaml_active_keys)!r}. "
        "Fix ragas_runner.llm_metrics() to set .name on each metric to match "
        "the eval_thresholds.yaml ragas.active keys."
    )


@pytest.mark.ragas
def test_eval_row_to_sample_defaults_reference_contexts():
    """EvalRow.to_sample() uses [reference] as reference_contexts when none given."""
    row = EvalRow(
        query="q",
        retrieved_contexts=["ctx"],
        answer="ans",
        reference="ref text",
    )
    sample = row.to_sample()
    assert sample.reference_contexts == ["ref text"]


@pytest.mark.ragas
def test_eval_row_to_sample_explicit_reference_contexts():
    """EvalRow.to_sample() preserves explicit reference_contexts."""
    row = EvalRow(
        query="q",
        retrieved_contexts=["ctx"],
        answer="ans",
        reference="ref text",
        reference_contexts=["explicit ctx A", "explicit ctx B"],
    )
    sample = row.to_sample()
    assert sample.reference_contexts == ["explicit ctx A", "explicit ctx B"]
