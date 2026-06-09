"""Encoder 抽象：Phase 0 決定性 mock；Phase 3 接真實 ColPali（colpali_service 的 gpu extra）。"""
import hashlib
import os

import numpy as np
from anatomy_shared.binary import binarize


def _seeded_vectors(text: str, n: int, dim: int = 128) -> np.ndarray:
    """以 query 文字雜湊播種，產生決定性 float 向量（mock 用）。"""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, dim)).astype("float32")


class MockEncoder:
    """決定性 mock：滿足 /encode_query 契約，供下游（後端 client、檢索）演練。"""

    ready = True
    model = "mock-colpali"

    def encode_query(self, q: str) -> dict:
        n_tokens = 20  # 典型 query token 數
        toks = _seeded_vectors(q, n_tokens)
        pooled = toks.mean(axis=0)
        return {
            "tokens_bin": [binarize(t) for t in toks],
            "pooled_bin": binarize(pooled),
            "model": self.model,
        }


def get_encoder():
    # Phase 3：ENCODER_MOCK=false 時回真實 ColPali encoder
    if os.environ.get("ENCODER_MOCK", "true").lower() == "true":
        return MockEncoder()
    # 真實 encoder 於 Phase 3 才實作；在此之前（如 make up-gpu 設 ENCODER_MOCK=false）
    # 應給出清楚指引，而非讓容器以難解的 ModuleNotFoundError 崩潰。
    try:
        from colpali_service.real_encoder import RealColPaliEncoder  # Phase 3 實作
    except ModuleNotFoundError as e:
        raise NotImplementedError(
            "真實 ColPali encoder 尚未實作（Phase 3）。目前僅支援 mock：請設 ENCODER_MOCK=true。"
            "（make up-gpu 的真實 GPU 推理路徑將於 Phase 3 接 vidore/colpali-v1.3-hf 後啟用；"
            "Phase 0 的 GPU 硬體驗證請用 make gpu-smoke）"
        ) from e
    return RealColPaliEncoder()
