"""Stage A — HNSW 粗排（§4.3、DL-013 Top-K=100、DL-019 halfvec cosine）。

呼叫端 MUST 先經 `hnsw_search_txn`（SET LOCAL ef_search + iterative_scan=strict_order
在同一 txn）。本函式只跑 SELECT。
"""
import json
from collections.abc import Sequence
from uuid import UUID

import asyncpg

from anatomy_shared.binary import pooled_to_halfvec_literal

_STAGE_A_SQL = """
SELECT page_id
FROM pages
WHERE kb_version = $2
  AND ($3::jsonb IS NULL OR metadata @> $3::jsonb)
ORDER BY pooled <=> $1::halfvec
LIMIT $4
"""


async def stage_a_coarse(
    conn: asyncpg.Connection,
    query_pooled: Sequence[float],
    metadata_filter: dict | None,
    kb_version: int,
    top_k: int = 100,
) -> list[UUID]:
    pooled_literal = pooled_to_halfvec_literal(query_pooled)
    meta = json.dumps(metadata_filter) if metadata_filter else None
    rows = await conn.fetch(_STAGE_A_SQL, pooled_literal, kb_version, meta, top_k)
    return [r["page_id"] for r in rows]
