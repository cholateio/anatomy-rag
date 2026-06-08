"""共用二值化（Phase 0 最小版；Phase 1 補跨語言一致性 / golden 測試）。

離線建庫端與線上 query 端 MUST import 同一個 binarize（§2.4 / CLAUDE.md）：
兩端若不一致，檢索精度會直接崩壞。本模組維持「純 numpy、不依賴 torch」（D-L），
讓 backend / CI 不被 torch 拖入。
"""
import numpy as np

VECTOR_DIM = 128  # ColPali 單向量維度 → bit(128)


def binarize(vec: np.ndarray) -> bytes:
    """將 128 維 float 向量以正負號二值化為 bit(128)，回傳 16 bytes（MSB-first）。

    bit_i = 1 if vec_i > 0 else 0；以 np.packbits 打包（每 8 bit 一 byte，共 16 bytes）。
    """
    arr = np.asarray(vec, dtype=np.float32).ravel()
    if arr.shape[0] != VECTOR_DIM:
        raise ValueError(f"binarize 期望 {VECTOR_DIM} 維向量，收到 {arr.shape[0]} 維")
    bits = (arr > 0).astype(np.uint8)
    return np.packbits(bits).tobytes()
