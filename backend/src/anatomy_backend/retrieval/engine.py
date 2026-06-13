"""引擎中立檢索介面（D-K）。

self-built（v1 baseline）用 binary tokens；VectorChord（Phase 12 PoC）用 float
multivector。兩者皆藏於本 Protocol 後，orchestrator 不感知內部實作。
retrieve() 回 EngineResult（含 §1.8 降級語意）。
"""
from typing import Protocol

import asyncpg

from .query_repr import QueryRepr
from .types import EngineResult


class RetrievalEngine(Protocol):
    async def retrieve(
        self,
        conn: asyncpg.Connection,
        query: QueryRepr,
        metadata_filter: dict | None,
        kb_version: int,
        top_k: int = 100,
        top_n: int = 10,
        stage_b_timeout_ms: int = 1000,
    ) -> EngineResult:
        """Stage A 粗排 → Stage B 精排（含逾時降級），回 EngineResult。"""
        ...
