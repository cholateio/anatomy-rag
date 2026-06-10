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
    邊界：NaN > 0 為 False → bit 0；±0 → bit 0（皆視為非正）。此約定離線/線上兩端一致即可。
    """
    arr = np.asarray(vec, dtype=np.float32).ravel()
    if arr.shape[0] != VECTOR_DIM:
        raise ValueError(f"binarize 期望 {VECTOR_DIM} 維向量，收到 {arr.shape[0]} 維")
    bits = (arr > 0).astype(np.uint8)
    return np.packbits(bits).tobytes()


def to_pg_bits(data: bytes) -> str:
    """將 binarize 產出的 bytes 轉為 PostgreSQL bit 字串（'0'/'1'，MSB-first）。

    PostgreSQL 沒有 bytea→bit 的 cast；SQL 綁定（§4.4 Stage B）一律經本函式轉成
    text 再 `::bit(128)`。位序與 binarize 的 np.packbits（MSB-first）為同一約定，
    集中在本檔以免離線端與 query 端各自轉換而漂移。
    """
    return "".join(f"{byte:08b}" for byte in data)
