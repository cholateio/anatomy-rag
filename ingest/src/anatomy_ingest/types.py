"""ingest 段間資料類別：來源 → 編碼 → 寫入。純資料、無 torch/DB 依賴。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

PATCH_BIN_BYTES = 16  # bit(128) = 16 bytes


@dataclass(frozen=True)
class PageParse:
    """來源段輸出（每頁）：Docling Markdown + 規範化 metadata。"""

    page_num: int
    markdown: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SourcePage:
    """來源 producer 的單頁產物：解析結果 + 待編碼/上傳的頁面影像。

    image 可為 None：解析出該頁但渲染缺圖時（pdf_source 不靜默丟棄，由 cli 記 render 失敗）。
    """

    parse: PageParse
    image: Any  # PIL.Image.Image | None（避免在 types 引 pillow 型別）


@dataclass(frozen=True)
class EncodedPage:
    """編碼段輸出（每頁）：patch 二值化串列 + pooled float32（halfvec 來源）。"""

    page_num: int
    patch_bins: list[bytes]
    pooled_f32: np.ndarray
    embed_model: str

    @property
    def n_patches(self) -> int:
        return len(self.patch_bins)

    def validate(self) -> "EncodedPage":
        if not self.patch_bins:
            raise ValueError(f"page {self.page_num}: 無任何 patch")
        for i, b in enumerate(self.patch_bins):
            if len(b) != PATCH_BIN_BYTES:
                raise ValueError(
                    f"page {self.page_num} patch {i}: 期望 {PATCH_BIN_BYTES} bytes，收到 {len(b)}"
                )
        arr = np.asarray(self.pooled_f32)
        if arr.shape != (128,):
            raise ValueError(f"page {self.page_num}: pooled 形狀須 (128,)，收到 {arr.shape}")
        return self


@dataclass(frozen=True)
class WriteOutcome:
    """單批寫入結果摘要（cli 報告用）。"""

    written: list[int] = field(default_factory=list)   # 成功寫入的 page_num
    failed: list[int] = field(default_factory=list)    # 寫入失敗（已記 ingest_errors）的 page_num
    skipped: list[int] = field(default_factory=list)   # resume 跳過的 page_num
