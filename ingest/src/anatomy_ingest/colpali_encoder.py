# ingest/src/anatomy_ingest/colpali_encoder.py
"""頁面影像 → EncodedPage：runtime 編碼 + shared.binary 二值化/池化（§2.4 單一來源）。

valid_mask=False（padding/特殊 token）的列**同時**排除於 patch 二值化與 pooled——
與 query 端一致，否則檢索精度崩壞（CLAUDE.md 共用二值化紅線）。
runtime 由呼叫端用 get_runtime(mock=...) 取得（torch 隔離維持在 shared/colpali_real）。
"""
from __future__ import annotations

import numpy as np
from anatomy_shared.binary import binarize, pool_patches

from .types import EncodedPage


def encode_page_image(runtime, image) -> EncodedPage:
    """單頁編碼。image：真實版 PIL.Image；mock 接受 str 鍵或 array-like。"""
    vecs = runtime.encode_page(image)
    mask = np.asarray(vecs.valid_mask, dtype=bool)
    valid = vecs.embeddings[mask]
    patch_bins = [binarize(v) for v in valid]
    pooled = pool_patches(vecs.embeddings, mask)  # pool_patches 自行套 mask
    page_num = getattr(image, "page_num", 0)  # 真實流程由 cli 覆寫 page_num（見 Task 9）
    return EncodedPage(
        page_num=page_num,
        patch_bins=patch_bins,
        pooled_f32=np.asarray(pooled, dtype=np.float32),
        embed_model=getattr(runtime, "model_id", "unknown"),
    ).validate()
