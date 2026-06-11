"""asyncpg 連線池——應用層唯一 DB 入口（PgBouncer :6432；§3.4）。

transaction pooling 紅線：statement_cache_size=0（禁 prepared statements）、
禁 LISTEN/NOTIFY、禁 temp tables、DB 連線不得跨 LLM 串流持有（DL-012，Phase 8 落實）。
Phase 2 不接 FastAPI lifespan（Phase 8 串 /chat 時一起接）；模組級單例 + 工廠分離，
測試可注入 Settings。
"""
import asyncio

import asyncpg

from anatomy_backend.config import Settings, get_settings

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def build_pool_kwargs(settings: Settings) -> dict:
    """組 asyncpg.create_pool 參數；紅線集中此處供單元測試斷言。"""
    return {
        "dsn": settings.database_url,
        "min_size": settings.db_pool_min_size,
        "max_size": settings.db_pool_max_size,
        "statement_cache_size": 0,
    }


async def create_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """建立新 pool（不走單例；測試/腳本用）。"""
    return await asyncpg.create_pool(**build_pool_kwargs(settings or get_settings()))


async def get_pool() -> asyncpg.Pool:
    """應用層共享單例（lazy；首呼叫建立）。

    單例綁定首個呼叫它的 event loop；測試請用 create_pool() 注入、不要碰單例。
    """
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await create_pool()
    return _pool


# 不持 _pool_lock：close 僅於 lifespan shutdown（嚴格時序）呼叫，與 get_pool 不併發。
async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
