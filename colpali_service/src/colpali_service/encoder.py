"""Encoder 抽象：mock（決定性，delegate 到 shared runtime）與真實路徑的工廠。"""
import os

from anatomy_shared.binary import binarize, pool_patches
from anatomy_shared.colpali_runtime import MockColPaliRuntime

from colpali_service.translate import detect_lang


class MockEncoder:
    """決定性 mock：滿足 /encode_query 契約，供下游（後端 client、檢索）演練。

    向量來源＝shared 的 MockColPaliRuntime；二值化/池化＝shared.binary（§2.4 單一來源）。
    """

    ready = True
    mt_model = "mock-identity"

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
            # DL-020：mock 為決定性 identity 翻譯（真實本地 MT 見 translate.py）
            "translated_q": q,
            "lang": detect_lang(q),
            "model": self.model,
            "mt_model": self.mt_model,
        }


def get_encoder():
    if os.environ.get("ENCODER_MOCK", "true").lower() == "true":
        return MockEncoder()
    try:
        from colpali_service.real_encoder import build_real_encoder
    except ImportError as e:
        raise RuntimeError(
            "真實 encoder 需要 gpu 依賴（torch/transformers/sentencepiece/OpenCC）："
            "請以 gpu extra 安裝（uv sync --package colpali-service --extra gpu）"
            "並用 make up-gpu 啟動；mock 請設 ENCODER_MOCK=true。"
        ) from e
    return build_real_encoder()
