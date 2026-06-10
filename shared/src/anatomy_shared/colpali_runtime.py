"""ColPali runtime 介面與決定性 mock（Phase 1）。

runtime 負責「輸入 → float32 向量矩陣 + valid_mask」（EncodedVectors）；二值化/池化
由呼叫端用 `anatomy_shared.binary` 組合（§2.4 單一來源）。真實 torch runtime
（ColPaliForRetrieval + ColPaliProcessor、bf16、SDPA、cu128）由 Phase 3 承接實作——
須先在 GPU 上驗 transformers 5.10.2 與 vidore/colpali-v1.3-hf 的相容性；
介面契約（encode_query/encode_page → EncodedVectors）自本檔起即為穩定契約。
本模組（mock 路徑）MUST 維持 torch-free（D-L）。
"""
import hashlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EncodedVectors:
    """runtime 輸出：embeddings (n,128) float32 + valid_mask (n,) bool。

    valid_mask=False 表示 padding 或特殊 token（應排除於池化/二值化之外）。
    """

    embeddings: np.ndarray
    valid_mask: np.ndarray


def _seeded_vectors(key: str, n: int, dim: int = 128) -> np.ndarray:
    """以字串雜湊播種，產生決定性 float32 向量（mock 用；query/頁面共用）。"""
    seed = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, dim)).astype("float32")


class MockColPaliRuntime:
    """決定性 mock：query → 20 token（前 2 個為特殊/前綴 token）、page → 64 patch（全有效）。"""

    model_id = "mock-colpali"
    n_query_tokens = 20
    n_special_prefix = 2   # 模擬 <bos>/任務前綴——池化與 tokens_bin 都應排除
    n_page_patches = 64    # mock 縮小規模以利測試；真實 ColPali 約 1024 patch/頁

    def encode_query(self, q: str) -> EncodedVectors:
        emb = _seeded_vectors(f"q::{q}", self.n_query_tokens)
        mask = np.ones(self.n_query_tokens, dtype=bool)
        mask[: self.n_special_prefix] = False
        return EncodedVectors(embeddings=emb, valid_mask=mask)

    def encode_page(self, image) -> EncodedVectors:
        """image：真實版收 PIL 影像；mock 另接受 str 鍵或 array-like（以位元組雜湊播種）。"""
        if isinstance(image, str):
            key = f"p::{image}"
        else:
            key = "p::" + hashlib.sha256(np.asarray(image).tobytes()).hexdigest()
        emb = _seeded_vectors(key, self.n_page_patches)
        return EncodedVectors(embeddings=emb, valid_mask=np.ones(self.n_page_patches, dtype=bool))


def get_runtime(mock: bool = True):
    if mock:
        return MockColPaliRuntime()
    raise NotImplementedError(
        "真實 ColPali runtime（ColPaliForRetrieval + ColPaliProcessor，bf16/SDPA/cu128）"
        "於 Phase 3 實作——需 GPU 並先驗 transformers 5.10.2 與 vidore/colpali-v1.3-hf "
        "相容性。目前請用 mock=True；介面契約（EncodedVectors）已固定。"
    )
