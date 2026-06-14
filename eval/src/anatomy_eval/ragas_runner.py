"""RAGAS 0.4.3 evaluation runner.

Three entry points:

``deterministic_metrics()``
    Returns a list of metrics that require **no LLM** and **no network**:
    NonLLMContextRecall, RougeScore, OutOfScopeCorrectness.  Pass these to
    ``run_eval(rows, metrics=deterministic_metrics())`` — llm=None is safe.

``llm_metrics()``
    Returns metrics for the real gate: Faithfulness, ResponseRelevancy,
    LLMContextPrecisionWithoutReference (renamed to ``context_precision``),
    and OutOfScopeCorrectness — exactly the four keys in
    ``eval_thresholds.yaml ragas.active``.  Faithfulness / ResponseRelevancy /
    LLMContextPrecisionWithoutReference **require** an LLM; pass
    ``llm=<provider>`` to ``run_eval``, or it will raise ``ValueError``
    (guard against the OpenAI fallback).

``run_eval(rows, *, metrics, llm=None, embeddings=None) -> dict[str, float]``
    Builds an EvaluationDataset from EvalRow objects, calls ragas evaluate(),
    and returns a dict mapping metric name → mean score across all rows.
    Raises ValueError if any metric needs an LLM but llm is None (prevents
    the ragas fallback that would call ``openai.OpenAI()``).

``build_rows_from_golden(golden, answer_provider) -> list[EvalRow]``
    Converts GoldenQA entries to EvalRow using an injected answer provider
    callable.  The provider returns (answer, retrieved_contexts) per question.

Import note: ``_ensure_compat()`` is called before ragas imports to patch the
missing ``langchain_community.chat_models.vertexai`` module required by
ragas 0.4.3 (see ``_ragas_compat.py``).

[C-1] RAGAS analytics opt-out: ``RAGAS_DO_NOT_TRACK=true`` is set via
``os.environ.setdefault`` here at module import time — BEFORE any ragas
module is loaded — so ``ragas._analytics.do_not_track()`` (which is
``lru_cache``'d on first call) always sees the flag and skips the
``requests.post`` to ``https://t.explodinggradients.com``.
"""
import os

# C-1: Disable RAGAS telemetry BEFORE importing ragas.
# ragas._analytics.do_not_track() is @lru_cache(maxsize=1) — it reads the env
# var only on its first call (which happens inside evaluate() → track()).
# Setting the var here guarantees the cache is populated with True before any
# evaluate() call, preventing any outbound HTTP to t.explodinggradients.com.
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

import typing as t
from collections import defaultdict
from dataclasses import dataclass, field

from anatomy_eval._ragas_compat import _ensure_compat
from anatomy_eval.golden import GoldenQA
from anatomy_eval.ragas_metrics import OutOfScopeCorrectness

_ensure_compat()  # MUST precede all ragas imports

from ragas.dataset_schema import (  # noqa: E402  # type: ignore[import]
    EvaluationDataset,
    SingleTurnSample,
)
from ragas.evaluation import evaluate  # noqa: E402  # type: ignore[import]
from ragas.metrics._context_recall import NonLLMContextRecall  # noqa: E402  # type: ignore[import]
from ragas.metrics._rouge_score import RougeScore  # noqa: E402  # type: ignore[import]
from ragas.metrics.base import MetricWithLLM  # noqa: E402  # type: ignore[import]

#: The OOS phrase used as reference for out_of_scope questions.
_OOS_REF = "教材中查無此項"

# Type alias for the answer provider callable used in build_rows_from_golden.
AnswerProvider = t.Callable[[GoldenQA], tuple[str, list[str]]]


@dataclass
class EvalRow:
    """A single evaluation unit for RAGAS metrics.

    Attributes:
        query: The user question (maps to SingleTurnSample.user_input).
        retrieved_contexts: Contexts retrieved by the RAG pipeline
            (maps to SingleTurnSample.retrieved_contexts and used by
            NonLLMContextRecall).
        answer: The LLM-generated answer (maps to SingleTurnSample.response).
        reference: Ground-truth reference string (maps to
            SingleTurnSample.reference; used by RougeScore and
            OutOfScopeCorrectness).
        is_oos: True if this question is out-of-scope.
        reference_contexts: Optional explicit reference contexts for
            NonLLMContextRecall.  Defaults to ``[reference]`` when None.
    """

    query: str
    retrieved_contexts: list[str]
    answer: str
    reference: str
    is_oos: bool = False
    reference_contexts: list[str] | None = field(default=None)

    def to_sample(self) -> SingleTurnSample:
        """Convert to ragas SingleTurnSample."""
        ref_ctxs = (
            self.reference_contexts
            if self.reference_contexts is not None
            else [self.reference]
        )
        return SingleTurnSample(
            user_input=self.query,
            response=self.answer,
            reference=self.reference,
            retrieved_contexts=self.retrieved_contexts,
            reference_contexts=ref_ctxs,
        )


# ── metric factories ───────────────────────────────────────────────────────────


def deterministic_metrics() -> list[t.Any]:
    """Return metrics that require no LLM and no network connection.

    Returns:
        [NonLLMContextRecall(), RougeScore(), OutOfScopeCorrectness()]

    All three metrics are pure-Python / string-distance based.  When passed to
    ``run_eval(rows, metrics=deterministic_metrics())``, llm=None is safe —
    ragas only creates an OpenAI client when a MetricWithLLM has llm=None, which
    none of these metrics are.
    """
    return [NonLLMContextRecall(), RougeScore(), OutOfScopeCorrectness()]


def llm_metrics() -> list[t.Any]:
    """Return metrics for gated real evaluation runs (all four YAML active keys).

    The returned metric names exactly match the ``ragas.active`` keys in
    ``eval_thresholds.yaml`` so that ``gate.check_ragas`` can look each up by
    name.  [C-2]: ``LLMContextPrecisionWithoutReference.name`` defaults to
    ``llm_context_precision_without_reference`` — we override it to
    ``context_precision`` (the YAML key) so the gate never sees a missing key.

    Three of the four metrics (Faithfulness / ResponseRelevancy /
    LLMContextPrecisionWithoutReference) require an injected ``llm``; passing
    these to ``run_eval`` without ``llm=`` will raise ``ValueError``.
    ``OutOfScopeCorrectness`` is a pure-Python metric but is included here so
    the real-run report covers all four active thresholds.

    Returns:
        List of four metrics with ``.name`` values:
        ``faithfulness``, ``answer_relevancy``, ``context_precision``,
        ``out_of_scope_correctness``.
    """
    from ragas.metrics._answer_relevance import ResponseRelevancy  # type: ignore[import]
    from ragas.metrics._context_precision import (  # type: ignore[import]
        LLMContextPrecisionWithoutReference,
    )
    from ragas.metrics._faithfulness import Faithfulness  # type: ignore[import]

    m_faith = Faithfulness()  # name already == "faithfulness" ✓
    m_relevancy = ResponseRelevancy()  # name already == "answer_relevancy" ✓
    m_precision = LLMContextPrecisionWithoutReference()
    m_precision.name = "context_precision"  # C-2: override to match YAML key
    m_oos = OutOfScopeCorrectness()  # name already == "out_of_scope_correctness" ✓
    return [m_faith, m_relevancy, m_precision, m_oos]


# ── core runner ───────────────────────────────────────────────────────────────


def run_eval(
    rows: list[EvalRow],
    *,
    metrics: list[t.Any],
    llm: t.Any | None = None,
    embeddings: t.Any | None = None,
    raise_exceptions: bool = True,
) -> dict[str, float]:
    """Evaluate a list of EvalRows with the given metrics.

    Args:
        rows: Evaluation rows to score.
        metrics: List of ragas Metric objects.
        llm: Optional BaseRagasLLM instance.  **Must** be provided if any
            metric in ``metrics`` is a MetricWithLLM; otherwise ValueError is
            raised to prevent the ragas fallback from calling openai.OpenAI().
        embeddings: Optional BaseRagasEmbeddings instance (required by
            ResponseRelevancy and other embedding-based metrics).
        raise_exceptions: Passed through to ragas evaluate().  True (default)
            surfaces metric errors in dev; False is accepted for the thin
            LLM-wiring test where NaN scores are tolerated.

    Returns:
        Dict mapping metric name to mean score across all rows.

    Raises:
        ValueError: If any metric requires an LLM but ``llm`` is None.
    """
    # Guard: raise before ragas can fall back to openai.OpenAI().
    needs_llm = [m for m in metrics if isinstance(m, MetricWithLLM)]
    if needs_llm and llm is None:
        names = [getattr(m, "name", type(m).__name__) for m in needs_llm]
        raise ValueError(
            f"Metrics {names} are LLM-based but llm=None was passed to run_eval. "
            "Provide an explicit llm= to prevent the ragas OpenAI fallback. "
            "Use deterministic_metrics() for offline/zero-cost evaluation."
        )

    dataset = EvaluationDataset(samples=[r.to_sample() for r in rows])
    result = evaluate(
        dataset,
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        show_progress=False,
        raise_exceptions=raise_exceptions,
    )

    # Aggregate: compute mean per metric across all samples.
    means: dict[str, list[float]] = defaultdict(list)
    for score_dict in result.scores:
        for k, v in score_dict.items():
            means[k].append(float(v))

    return {k: sum(vs) / len(vs) for k, vs in means.items()}


# ── golden → EvalRow conversion ───────────────────────────────────────────────


def build_rows_from_golden(
    golden: list[GoldenQA],
    answer_provider: AnswerProvider,
) -> list[EvalRow]:
    """Build EvalRow list from GoldenQA entries using an injected answer provider.

    The ``answer_provider`` is called once per question and must return a tuple
    ``(answer: str, retrieved_contexts: list[str])``.  In CI/mock mode this is
    a canned function; in real evaluation mode it calls the live /chat pipeline.

    For out-of-scope questions:
        reference = "教材中查無此項"  (expected output of the system)
    For in-scope questions:
        reference = " ".join(expected_concepts) if expected_concepts
                    else " ".join(expected_pages)   (proxy reference)

    Args:
        golden: List of validated GoldenQA items (from load_golden).
        answer_provider: Callable(qa) → (answer, retrieved_contexts).

    Returns:
        List of EvalRow, one per golden entry.
    """
    rows: list[EvalRow] = []
    for qa in golden:
        answer, retrieved_contexts = answer_provider(qa)
        is_oos = qa.category == "out_of_scope"
        if is_oos:
            reference = _OOS_REF
        else:
            # Use expected_concepts as a proxy reference; fall back to page IDs.
            if qa.expected_concepts:
                reference = " ".join(qa.expected_concepts)
            else:
                reference = " ".join(qa.expected_pages)
        rows.append(
            EvalRow(
                query=qa.query,
                retrieved_contexts=retrieved_contexts,
                answer=answer,
                reference=reference,
                is_oos=is_oos,
            )
        )
    return rows
