"""檢索回傳型別（§4.7 介面契約 + §1.8 降級語意）。"""
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RetrievalResult:
    page_id: UUID
    score: float            # RRF 融合分數
    book_title: str
    edition: str | None
    page_num: int
    page_image_uri: str     # S3 / MinIO 路徑（內部）
    docling_md: str
    metadata: dict          # JSONB，含 figures 等


@dataclass(frozen=True)
class EngineResult:
    """引擎向量檢索輸出（§1.8 降級用）。

    ranked：Stage B（或原生 MaxSim）排名 (page_id, score)；degraded 時為空。
    coarse_ids：Stage A 距離遞增排序的候選頁；degraded 時 orchestrator 取其 top-N 當降級結果。
    degraded：Stage B 逾時/失敗、改用 Stage A 排序時為 True（§1.8，供 Phase 9 trace 標記）。
    """
    ranked: list[tuple[UUID, float]]
    coarse_ids: list[UUID]
    degraded: bool
