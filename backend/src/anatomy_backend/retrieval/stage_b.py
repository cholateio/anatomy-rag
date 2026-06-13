"""Stage B — MaxSim 精排（§4.4）。

score(page) = Σ_t max_p (128 - hamming(token_t, patch_p))

兩條等價路徑（§4.4）：
- stage_b_maxsim：SQL 聚合（pgvector `<~>` Hamming），spec 主路徑。
- stage_b_maxsim_numpy：撈候選頁 patch_bin 後應用層 numpy XOR+popcount（並發退路）。
預設由 Stage B 並發/p95 benchmark gate 決定（見 SelfBuiltEngine.stage_b_mode）。

query tokens 經 shared.binary.to_pg_bits（唯一位序轉換點）轉 128-char '0'/'1' → ::bit(128)。
MUST 只掃候選頁、MUST 帶 kb_version（DL-017 分區）。
"""
from collections.abc import Sequence
from uuid import UUID

import asyncpg

from anatomy_shared.binary import to_pg_bits

_STAGE_B_SQL = """
WITH query_tokens AS (
    SELECT token_idx, q_bits::bit(128) AS q_bin
    FROM unnest($1::text[]) WITH ORDINALITY AS qt(q_bits, token_idx)
),
token_max_per_page AS (
    SELECT pp.page_id, qt.token_idx,
           MAX(128 - (pp.patch_bin <~> qt.q_bin))::float AS sim
    FROM page_patches pp
    JOIN query_tokens qt ON true
    WHERE pp.page_id = ANY($2::uuid[])
      AND pp.kb_version = $3
    GROUP BY pp.page_id, qt.token_idx
)
SELECT page_id, SUM(sim) AS maxsim_score
FROM token_max_per_page
GROUP BY page_id
ORDER BY maxsim_score DESC
LIMIT $4
"""


async def stage_b_maxsim(
    conn: asyncpg.Connection,
    candidate_page_ids: list[UUID],
    query_tokens_bin: Sequence[bytes],
    kb_version: int,
    top_n: int = 10,
) -> list[tuple[UUID, float]]:
    if not candidate_page_ids:
        return []
    q_bits = [to_pg_bits(t) for t in query_tokens_bin]
    rows = await conn.fetch(_STAGE_B_SQL, q_bits, candidate_page_ids, kb_version, top_n)
    return [(r["page_id"], float(r["maxsim_score"])) for r in rows]
