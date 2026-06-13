"""條件式附圖路由（DL-009 / §5.5）。

表驅動：query intent + page_type → 送幾張圖、detail。Phase 6 只決定「送幾張/哪張/
detail」；真正 fetch 圖 bytes 在 Phase 8 orchestrator。intent 由上游分類器決定
（Phase 8，OPEN），本層僅消費。

DL-009：純文字題 0 圖；圖譜題只對 figure_heavy/mixed 頁送圖、**預設 top-1、硬上限 2**。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anatomy_backend.retrieval.types import RetrievalResult

_IMAGE_PAGE_TYPES = frozenset({"figure_heavy", "mixed"})
DEFAULT_IMAGE_COUNT = 1   # DL-009 預設 top-1
DL009_MAX_IMAGES = 2      # DL-009 硬上限（非固定 3）


class QueryIntent(str, Enum):
    PURE_TEXT = "pure_text"  # 純文字/概念題 → 不送圖（最大成本槓桿）
    FIGURE = "figure"        # 圖譜/判讀題 → 條件式送圖


@dataclass(frozen=True)
class ImageRoutingDecision:
    """indices：要附圖的 results 索引（RRF 既有順序）；空＝不送圖。
    detail：附圖時的 OpenAI detail（需判讀標籤 → high）。"""

    indices: tuple[int, ...]
    detail: str = "high"


def route_images(
    results: list[RetrievalResult],
    intent: QueryIntent,
    max_images: int = DEFAULT_IMAGE_COUNT,
) -> ImageRoutingDecision:
    """依 intent + page_type 路由。純文字題 0 圖；圖譜題取前 N 個 figure_heavy/mixed 頁
    （N = min(max_images, DL009_MAX_IMAGES)）、detail=high。"""
    if intent == QueryIntent.PURE_TEXT:
        return ImageRoutingDecision(indices=())
    cap = min(max_images, DL009_MAX_IMAGES)
    indices: list[int] = []
    for i, r in enumerate(results):
        if r.metadata.get("page_type") in _IMAGE_PAGE_TYPES:
            indices.append(i)
            if len(indices) >= cap:
                break
    return ImageRoutingDecision(indices=tuple(indices), detail="high")
