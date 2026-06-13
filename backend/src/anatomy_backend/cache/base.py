"""語意快取 seam（§6.4）。v1 Phase 8 只定義介面 + NoOpCache（永遠 miss）；
真 SemanticCache（redisvl + 本地 embedding、只快取已驗證答案）為 Phase 7。
DL-021：追問不查/不寫快取——由 chat.py 控制，不在此。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CachedAnswer:
    answer: str
    sources: list[dict]   # sources＝PageCitation.model_dump() 串列


class CacheProtocol(Protocol):
    async def get(
        self, query: str, kb_version: int, metadata_filter: dict | None = None
    ) -> CachedAnswer | None: ...

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
        metadata_filter: dict | None = None,
    ) -> None: ...


class NoOpCache:
    async def get(
        self, query: str, kb_version: int, metadata_filter: dict | None = None
    ) -> CachedAnswer | None:
        return None

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
        metadata_filter: dict | None = None,
    ) -> None:
        return None


def build_cache(settings, redis_client=None) -> CacheProtocol:
    """依設定回傳快取實作（DL-025）。

    cache_enabled=False 或無 redis_client → NoOpCache（退路）。
    cache_mode="exact"（v1 預設）→ SemanticCache（exact-normalized-query）。
    cache_mode="semantic" → 後續 config 開關，尚未實作（需 fastembed，torch-free）。
    """
    if not getattr(settings, "cache_enabled", True) or redis_client is None:
        return NoOpCache()
    mode = getattr(settings, "cache_mode", "exact")
    if mode == "exact":
        from anatomy_backend.cache.semantic_cache import SemanticCache

        return SemanticCache(redis_client, ttl_seconds=settings.cache_ttl_seconds)
    if mode == "semantic":
        raise NotImplementedError(
            "cache_mode='semantic' 向量比對尚未啟用（需 fastembed，torch-free；見 DL-025 / DL-012）"
        )
    raise ValueError(f"未知 cache_mode：{mode!r}")
