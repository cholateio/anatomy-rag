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


@dataclass(frozen=True, eq=False)
class EncodedVectors:
    """runtime 輸出：embeddings (n,128) float32 + valid_mask (n,) bool。

    valid_mask=False 表示 padding 或特殊 token（應排除於池化/二值化之外）。
    eq=False：ndarray 欄位不支援值比較，比較請用 np.array_equal。

    真實 runtime：valid_mask=attention_mask（排除 batch padding；前綴/augmentation tokens
    是否納入跟隨 processor 原生行為）。token 數隨輸入而異，呼叫端不得假設固定數量。
    """

    embeddings: np.ndarray
    valid_mask: np.ndarray


# 預期 tied-weights 缺失 key（lm_head 與 embed_tokens 權重綁定；colpali-engine v0.3.14 同樣忽略）。
# 精確 allowlist 而非子字串比對——避免吞掉真實壞損。
EXPECTED_TIED_MISSING_KEYS = frozenset({
    "lm_head.weight", "model.lm_head.weight", "vlm.lm_head.weight",
})


def check_loading_info(loading_info: dict,
                       expected_missing: frozenset = EXPECTED_TIED_MISSING_KEYS) -> None:
    """`from_pretrained(output_loading_info=True)` 結果守門——
    「載入無 uninitialized weights 警告」（roadmap Phase 3 AC）的程式化版本。

    除精確列名的預期 tied weights 外，任何 missing/unexpected/mismatched/error_msgs
    一律 RuntimeError fail-fast。純 dict 驗證、torch-free，供單元測試直測。
    """
    missing = [k for k in loading_info.get("missing_keys", []) if k not in expected_missing]
    unexpected = list(loading_info.get("unexpected_keys", []))
    mismatched = list(loading_info.get("mismatched_keys", []))
    errors = list(loading_info.get("error_msgs", []))
    if missing or unexpected or mismatched or errors:
        raise RuntimeError(
            "ColPali 權重載入異常（uninitialized weights 守門）："
            f"missing={missing} unexpected={unexpected} mismatched={mismatched} errors={errors}"
        )


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


def get_runtime(mock: bool = True, **kwargs):
    """mock=True → MockColPaliRuntime；mock=False → 真實 runtime（lazy import torch，D-L）。

    kwargs 透傳 RealColPaliRuntime（model_id / device / dtype）。
    """
    if mock:
        return MockColPaliRuntime()
    try:
        from anatomy_shared.colpali_real import RealColPaliRuntime
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "真實 ColPali runtime 需要 gpu 依賴：請以 colpali extra 安裝 "
            "（uv sync --package colpali-service --extra gpu）或使用 make up-gpu。"
        ) from e
    return RealColPaliRuntime(**kwargs)
