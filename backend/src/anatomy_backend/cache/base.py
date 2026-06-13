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
    async def get(self, query: str, kb_version: int) -> CachedAnswer | None: ...

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
    ) -> None: ...


class NoOpCache:
    async def get(self, query: str, kb_version: int) -> CachedAnswer | None:
        return None

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
    ) -> None:
        return None


def build_cache(settings) -> CacheProtocol:
    return NoOpCache()  # Phase 7 改回真 SemanticCache（依 settings）
