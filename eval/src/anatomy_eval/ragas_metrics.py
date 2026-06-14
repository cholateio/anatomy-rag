"""Pure-Python ragas metric: OutOfScopeCorrectness (no LLM required).

教材中查無此項 OOS 正確性指標：量測生成端（response）對「教材中查無此項」
回應的正確性——response 與 reference 對 OOS 短語的出現/不出現一致時得 1.0，
否則得 0.0。純 Python 實作，不需要 LLM 或向量模型。

Usage::

    from anatomy_eval.ragas_metrics import OutOfScopeCorrectness, OOS_PHRASE

Import note: ``_ensure_compat()`` must be called before any ``ragas.*`` import
because ragas 0.4.3 does a top-level import of
``langchain_community.chat_models.vertexai.ChatVertexAI`` which is absent in
langchain-community ≥ 0.3.  The call below patches ``sys.modules`` before ragas
is first imported from this module.
"""
from dataclasses import dataclass, field

from anatomy_eval._ragas_compat import _ensure_compat

_ensure_compat()  # MUST precede all ragas imports

from langchain_core.callbacks import Callbacks  # noqa: E402  # type: ignore[import]
from ragas.dataset_schema import SingleTurnSample  # noqa: E402  # type: ignore[import]
from ragas.metrics.base import (  # noqa: E402  # type: ignore[import]
    MetricOutputType,
    MetricType,
    SingleTurnMetric,
)
from ragas.run_config import RunConfig  # noqa: E402  # type: ignore[import]

#: The canonical out-of-scope phrase matched in response and reference.
OOS_PHRASE: str = "教材中查無此項"


@dataclass
class OutOfScopeCorrectness(SingleTurnMetric):
    """Pure-Python SingleTurnMetric: 1.0 iff response/reference agree on OOS phrase.

    score = float(said == should) where::

        said   = OOS_PHRASE in (sample.response or "")
        should = OOS_PHRASE in (sample.reference or "")

    Requires only ``response`` and ``reference`` columns — no LLM, no network.
    """

    name: str = "out_of_scope_correctness"
    _required_columns: dict[MetricType, set[str]] = field(
        default_factory=lambda: {MetricType.SINGLE_TURN: {"response", "reference"}}
    )
    output_type: MetricOutputType = MetricOutputType.CONTINUOUS

    def init(self, run_config: RunConfig) -> None:  # noqa: D102
        pass

    async def _single_turn_ascore(
        self, sample: SingleTurnSample, callbacks: Callbacks
    ) -> float:
        said: bool = OOS_PHRASE in (sample.response or "")
        should: bool = OOS_PHRASE in (sample.reference or "")
        return float(said == should)

    async def _ascore(self, row: dict, callbacks: Callbacks) -> float:
        return await self._single_turn_ascore(SingleTurnSample(**row), callbacks)
