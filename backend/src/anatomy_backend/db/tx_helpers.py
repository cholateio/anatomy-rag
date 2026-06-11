"""Stage A 查詢的 transaction helper（D-G）。

PgBouncer transaction pooling 下，只有「同一 transaction」能保證 SET LOCAL 與
後續 SELECT 落在同一個 Postgres 後端連線；分開執行會各自拿到不同 server conn，
ef_search 形同未設。Stage A（Phase 5）MUST 經本 helper 跑 HNSW 查詢。

iterative_scan=strict_order（pgvector ≥0.8）：pages_pooled_hnsw 是跨 kb_version 的
全域索引，Stage A 必帶 WHERE kb_version 過濾；非 iterative 模式下 HNSW 先取
ef_search 個候選、過濾後才 LIMIT——blue-green 雙版本期會撈不滿 Top-K=100，
直接傷 DL-013 recall gate。strict_order 讓索引持續掃描直到湊滿 LIMIT。
"""
from contextlib import asynccontextmanager

_EF_SEARCH_MAX = 1000  # pgvector 合法範圍 1..1000；超過必為呼叫端錯誤


@asynccontextmanager
async def hnsw_search_txn(pool, ef_search: int = 100):
    """取得連線並開啟 transaction，SET LOCAL ef_search + iterative_scan 後交出 conn。"""
    if type(ef_search) is not int or not (1 <= ef_search <= _EF_SEARCH_MAX):
        raise ValueError(f"ef_search 必須為 1..{_EF_SEARCH_MAX} 的整數，收到 {ef_search!r}")
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 數值已驗證為純 int；SET 不支援參數綁定故用 f-string
            await conn.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
            await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            yield conn
