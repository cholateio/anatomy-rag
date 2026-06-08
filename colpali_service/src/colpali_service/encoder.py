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
    from colpali_service.real_encoder import RealColPaliEncoder  # Phase 3 實作
    return RealColPaliEncoder()
