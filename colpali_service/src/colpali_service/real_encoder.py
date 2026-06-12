"""§4.2 /encode_query 契約的真實實作：MT（DL-020）→ ColPali 編碼 → shared 二值化/池化。

組合物件本身無 torch import；重依賴在 build_real_encoder() 內經 get_runtime(mock=False)
/ build_marian_translator() lazy 取得（單元測試以 mock runtime + fake translator 注入）。
"""
import os

from anatomy_shared.binary import binarize, pool_patches

from colpali_service.translate import DEFAULT_MT_MODEL, LocalTranslator


class RealColPaliEncoder:
    """與 MockEncoder 同契約：encode_query(q) -> dict（main.py 負責 base64）。"""

    def __init__(self, runtime, translator: LocalTranslator):
        self._runtime = runtime
        self._translator = translator
        self.model = runtime.model_id
        self.mt_model = translator.mt_model_name

    def encode_query(self, q: str) -> dict:
        tr = self._translator.translate(q)
        # DL-020：zh 且 MT 成功 → 以英文編碼；MT 失敗 → 原文編碼；en → 原文
        text_for_model = tr.translated_q if (tr.lang == "zh" and tr.translated_q) else q
        enc = self._runtime.encode_query(text_for_model)
        valid = enc.embeddings[enc.valid_mask]
        pooled_f32 = pool_patches(enc.embeddings, valid_mask=enc.valid_mask).astype("<f4")
        return {
            "tokens_bin": [binarize(t) for t in valid],
            "pooled_f32": pooled_f32.tobytes(),
            "translated_q": tr.translated_q,
            "lang": tr.lang,
            "model": self.model,
            "mt_model": tr.mt_model,
        }


def build_real_encoder() -> RealColPaliEncoder:
    """從環境組真實 encoder（GPU 容器路徑）。"""
    from anatomy_shared.colpali_runtime import get_runtime

    from colpali_service.translate import build_marian_translator

    runtime = get_runtime(
        mock=False,
        model_id=os.environ.get("COLPALI_MODEL", "vidore/colpali-v1.3-hf"),
        device=os.environ.get("COLPALI_DEVICE", "cuda"),
    )
    translator = build_marian_translator(os.environ.get("MT_MODEL", DEFAULT_MT_MODEL))
    return RealColPaliEncoder(runtime=runtime, translator=translator)
