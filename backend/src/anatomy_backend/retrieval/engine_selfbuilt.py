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
    """self-built 兩階段引擎。stage_b_mode（DL-024）：
    - 'sql'（DEFAULT，§4.4 主路徑）：Phase 5 並發 benchmark 證實 SQL 恆優於 numpy。
    - 'numpy'（已測非預設替代）：§4.4 應用層 XOR+popcount。Phase 5 benchmark（DL-024）
      推翻其作為「並發退路」的假設——每個並發層級皆比 SQL 慢（單查詢 184 vs 159ms；
      c32 達 6.5s，因持連線做 Python 計算 + 大量 BitString→bytes + GIL 爭用）。保留為
      §4.7 介面後的已測參考，不作預設、不作並發退路。
    """
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
        baseline_timeout = await conn.fetchval("SELECT current_setting('statement_timeout')")
        try:
            # savepoint 隔離：Stage B 逾時/錯誤只回滾本子交易，外層 txn 存活
            # → BM25 / metadata fetch 仍可在同連線跑（§1.8 降級，非整請求失敗）。
            # statement_timeout 只綁 Stage B：savepoint RELEASE 會把子交易的 SET LOCAL 帶到
            # 外層 txn，故成功路徑後還原為進入前的基線值（非歸零——否則摧毀 role/db/呼叫端
            # 既有的 statement_timeout 護欄）。
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('statement_timeout', $1, true)",
                    str(stage_b_timeout_ms),
                )
                ranked = await _STAGE_B[self.stage_b_mode](
                    conn, coarse, list(query.tokens_bin), kb_version, top_n
                )
            # savepoint RELEASE 會把 Stage B 的 SET LOCAL 帶到外層 txn；還原成進入前的
            # 基線值（非歸零——否則摧毀 role/db/呼叫端既有的 statement_timeout 護欄）。
            await conn.execute(
                "SELECT set_config('statement_timeout', $1, true)", baseline_timeout)
            return EngineResult(ranked=ranked, coarse_ids=coarse, degraded=False)
        except asyncpg.QueryCanceledError:
            # §1.8：Stage B statement_timeout 觸發 → 退回 Stage A 排序（degrade 路徑 savepoint
            # rollback 已自動還原 statement_timeout）。只攔逾時取消，其餘 PostgresError 照常上拋
            # （缺 operator / 缺分區 / 型別錯等真 bug 不可被誤吞成降級）。
            return EngineResult(ranked=[], coarse_ids=coarse, degraded=True)
