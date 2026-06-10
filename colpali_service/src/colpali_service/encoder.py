"""Encoder 抽象：Phase 0/1 決定性 mock（delegate 到 shared runtime）。

Phase 3 接真實 ColPali + 本地 MT（DL-020）。
"""
import os
import re

from anatomy_shared.binary import binarize, pool_patches
from anatomy_shared.colpali_runtime import MockColPaliRuntime

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


def _detect_lang(text: str) -> str:
    """含 CJK 字元即視為需翻譯的中文/混語 query（DL-020）。"""
    return "zh" if _CJK_RE.search(text) else "en"


class MockEncoder:
    """決定性 mock：滿足 /encode_query 契約，供下游（後端 client、檢索）演練。

    向量來源＝shared 的 MockColPaliRuntime；二值化/池化＝shared.binary（§2.4 單一來源）。
    """

    ready = True

    def __init__(self) -> None:
        self._runtime = MockColPaliRuntime()
        self.model = self._runtime.model_id

    def encode_query(self, q: str) -> dict:
        enc = self._runtime.encode_query(q)
        valid = enc.embeddings[enc.valid_mask]   # 排除 padding/特殊前綴 token（§2.4 / roadmap AC）
        # DL-019：pooled 不二值化、全程 fp32（halfvec 量化只發生在 DB 綁定層）
        pooled_f32 = pool_patches(enc.embeddings, valid_mask=enc.valid_mask).astype("<f4")
        return {
            "tokens_bin": [binarize(t) for t in valid],
            "pooled_f32": pooled_f32.tobytes(),
            # DL-020：mock 為決定性 identity 翻譯（真實本地 MT 於 Phase 3 接 opus-mt-zh-en）
            "translated_q": q,
            "lang": _detect_lang(q),
            "model": self.model,
            "mt_model": "mock-identity",
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
