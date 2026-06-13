"""檢索主入口（§4.7）。

單一 PgBouncer 連線 + 單一 transaction（hnsw_search_txn）序列跑
Stage A → Stage B → BM25 → 單一 SQL metadata fetch（DL-002）。連線用完即還，
不跨 LLM 串流（Phase 8 落實串流，此處設計先對）。
"""
import json

import asyncpg

from ..db.tx_helpers import hnsw_search_txn
from .bm25 import bm25_search
from .engine import RetrievalEngine
from .engine_selfbuilt import SelfBuiltEngine
from .query_repr import QueryRepr
from .rrf import rrf_fuse
from .types import RetrievalResult

_METADATA_SQL = """
SELECT p.page_id, p.page_num, p.page_image_uri, p.docling_md, p.metadata,
       b.title AS book_title, b.edition
FROM pages p JOIN books b USING (book_id)
WHERE p.page_id = ANY($1::uuid[]) AND p.kb_version = $2
"""


async def retrieve(
    pool: asyncpg.Pool,
    query: str,
    query_repr: QueryRepr,
    metadata_filter: dict | None,
    kb_version: int,
    top_n: int = 3,
    engine: RetrievalEngine | None = None,
    ef_search: int = 100,
) -> list[RetrievalResult]:
    engine = engine or SelfBuiltEngine()
    async with hnsw_search_txn(pool, ef_search=ef_search) as conn:
        # DL-002：單一 conn 序列（asyncpg 禁同連線併發）
        er = await engine.retrieve(
            conn, query_repr, metadata_filter, kb_version, top_k=100, top_n=10)
        # §1.8 降級：Stage B 逾時/失敗（er.degraded）→ 用 Stage A 排序 top-N 餵 RRF；
        # er.degraded 供 Phase 9 trace 標記（此處不另外 log）
        vector_ids = [pid for pid, _ in er.ranked] if er.ranked else er.coarse_ids[:10]
        bm25_q = query_repr.translated_q or query                  # DL-013/DL-020
        bm25_res = await bm25_search(conn, bm25_q, kb_version, top_k=50)
        fused = rrf_fuse([vector_ids, bm25_res])
        final = fused[:top_n]
        final_ids = [pid for pid, _ in final]
        final_scores = dict(final)
        if not final_ids:
            return []
        rows = await conn.fetch(_METADATA_SQL, final_ids, kb_version)
    by_id = {r["page_id"]: r for r in rows}                        # IN 不保序
    out: list[RetrievalResult] = []
    for pid in final_ids:                                          # 依 RRF 順序重排
        r = by_id.get(pid)
        if r is None:
            continue
        meta = r["metadata"]
        out.append(RetrievalResult(
            page_id=pid, score=final_scores[pid],
            book_title=r["book_title"], edition=r["edition"],
            page_num=r["page_num"], page_image_uri=r["page_image_uri"],
            docling_md=r["docling_md"],
            metadata=meta if isinstance(meta, dict) else json.loads(meta)))
    return out
