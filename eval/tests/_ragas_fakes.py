"""Offline fake implementations of BaseRagasLLM and BaseRagasEmbeddings.

Used ONLY for the thin LLM-wiring test (``test_llm_wiring_runs_without_network``).
These fakes make NO network calls.  The fake LLM's canned response may not match
every metric's expected JSON schema, so NaN scores are acceptable for the wiring
test.  Never use these fakes to assert exact metric values [H-3].
"""
# Ensure compat stub is applied before any ragas import.
from anatomy_eval._ragas_compat import _ensure_compat

_ensure_compat()

import hashlib  # noqa: E402
import math  # noqa: E402
import typing as t  # noqa: E402
from dataclasses import dataclass  # noqa: E402

from langchain_core.outputs import Generation, LLMResult  # noqa: E402  # type: ignore[import]
from ragas.embeddings.base import BaseRagasEmbeddings  # noqa: E402  # type: ignore[import]
from ragas.llms.base import BaseRagasLLM  # noqa: E402  # type: ignore[import]
from ragas.run_config import RunConfig  # noqa: E402  # type: ignore[import]

# Seed vector dimension (tiny — we only care about wiring, not cosine quality).
_DIM = 8

# Canned JSON that is a best-effort approximation of ragas metric schemas.
# If a particular metric's parser rejects it, the score becomes NaN which is
# acceptable for the wiring test (raise_exceptions=False path).
_CANNED_JSON = (
    '{"statements": [], "verdicts": [], "questions": ["What is anatomy?"], '
    '"noncommittal": false}'
)


@dataclass
class FakeRagasLLM(BaseRagasLLM):
    """Synchronous + async fake LLM that returns a canned JSON Generation.

    Never makes network calls.  Passed to ``run_eval(…, llm=FakeRagasLLM())``
    in the LLM-wiring test so that ragas does not fall back to ``openai.OpenAI()``.
    """

    def generate_text(  # noqa: D102
        self,
        prompt: t.Any,
        n: int = 1,
        temperature: float = 0.01,
        stop: list[str] | None = None,
        callbacks: t.Any = None,
    ) -> LLMResult:
        return LLMResult(generations=[[Generation(text=_CANNED_JSON)]])

    async def agenerate_text(  # noqa: D102
        self,
        prompt: t.Any,
        n: int = 1,
        temperature: float | None = 0.01,
        stop: list[str] | None = None,
        callbacks: t.Any = None,
    ) -> LLMResult:
        return LLMResult(generations=[[Generation(text=_CANNED_JSON)]])

    def is_finished(self, response: LLMResult) -> bool:  # noqa: D102
        return True


class FakeRagasEmbeddings(BaseRagasEmbeddings):
    """Deterministic seed-vector embeddings.  Never makes network calls.

    Implements both sync and async embedding methods required by
    ``BaseRagasEmbeddings`` (which extends langchain ``Embeddings``).
    """

    # run_config is required by BaseRagasEmbeddings internals.
    run_config: RunConfig = RunConfig()  # type: ignore[assignment]

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _seed_vec(text: str) -> list[float]:
        """Return a deterministic unit-ish vector seeded by md5(text)."""
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % (2**31)
        # Simple LCG — no numpy dependency needed for fake embeddings.
        state = seed
        vec: list[float] = []
        for _ in range(_DIM):
            state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
            vec.append((state / 0xFFFFFFFF) * 2.0 - 1.0)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    # ── sync (langchain Embeddings ABC) ────────────────────────────────────────

    def embed_query(self, text: str) -> list[float]:  # noqa: D102
        return self._seed_vec(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:  # noqa: D102
        return [self._seed_vec(txt) for txt in texts]

    # ── async (BaseRagasEmbeddings ABC) ────────────────────────────────────────

    async def aembed_query(self, text: str) -> list[float]:  # noqa: D102
        return self._seed_vec(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:  # noqa: D102
        return [self._seed_vec(txt) for txt in texts]
