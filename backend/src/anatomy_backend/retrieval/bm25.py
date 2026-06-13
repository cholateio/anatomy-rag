"""BM25 文字副線（§4.5；tsvector simple config + ts_rank_cd）。"""
from uuid import UUID

import asyncpg

_BM25_SQL = """
SELECT page_id
FROM pages
WHERE text_tsv @@ plainto_tsquery('simple', $1) AND kb_version = $2
ORDER BY ts_rank_cd(text_tsv, plainto_tsquery('simple', $1)) DESC
LIMIT $3
"""


async def bm25_search(
    conn: asyncpg.Connection, query: str, kb_version: int, top_k: int = 50
) -> list[UUID]:
    rows = await conn.fetch(_BM25_SQL, query, kb_version, top_k)
    return [r["page_id"] for r in rows]
