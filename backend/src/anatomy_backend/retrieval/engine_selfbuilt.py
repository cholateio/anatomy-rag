"""self-built pgvector 兩階段引擎（v1 baseline，DL-014）。"""
from dataclasses import dataclass

import asyncpg

from .query_repr import QueryRepr
from .stage_a import stage_a_coarse
from .stage_b import stage_b_maxsim, stage_b_maxsim_numpy
from .types import EngineResult

_STAGE_B = {"sql": stage_b_maxsim, "numpy": stage_b_maxsim_numpy}


@dataclass
class SelfBuiltEngine:
    """stage_b_mode: 'sql'（spec 主路徑）| 'numpy'（並發退路）；預設由 benchmark gate 決定。"""
    stage_b_mode: str = "sql"

    def __post_init__(self) -> None:
        if self.stage_b_mode not in _STAGE_B:
            raise ValueError(f"stage_b_mode 必須為 {set(_STAGE_B)}，收到 {self.stage_b_mode!r}")

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
        if not query.has_binary_tokens:
            raise ValueError("SelfBuiltEngine 需要 binary tokens（QueryRepr.tokens_bin 為空）")
        coarse = await stage_a_coarse(
            conn, query.pooled_f32, metadata_filter, kb_version, top_k)
        if not coarse:
            return EngineResult(ranked=[], coarse_ids=[], degraded=False)
        try:
            # savepoint 隔離：Stage B 逾時/錯誤只回滾本子交易，外層 txn 存活
            # → BM25 / metadata fetch 仍可在同連線跑（§1.8 降級，非整請求失敗）。
            # statement_timeout 綁住 SQL 路徑全程與 numpy 路徑的 DB fetch；numpy 的
            # 純 Python popcount（K=100 約 1.6MB、sub-10ms）不在 PG 逾時內，benchmark 證實其快。
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('statement_timeout', $1, true)",
                    str(stage_b_timeout_ms))
                ranked = await _STAGE_B[self.stage_b_mode](
                    conn, coarse, list(query.tokens_bin), kb_version, top_n)
            return EngineResult(ranked=ranked, coarse_ids=coarse, degraded=False)
        except asyncpg.PostgresError:
            # §1.8：Stage B timeout > 1s → 退回 Stage A 排序（orchestrator 取 coarse top-N）
            return EngineResult(ranked=[], coarse_ids=coarse, degraded=True)
