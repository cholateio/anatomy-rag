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

    asyncpg 綁定注意：純量 str 參數 MUST 寫 `$N::text::bit(128)`——bare `$N::bit(128)` 會讓
    asyncpg 對 str 套 bit codec 而報錯（需 bytes/BitString）。§4.4 Stage B 的
    `unnest($1::text[])` 路徑因元素已是 text 型別，單層 `::bit(128)` 即可。
    """
    return "".join(f"{byte:08b}" for byte in data)


def pool_patches(patch_embs, valid_mask=None) -> np.ndarray:
    """多向量 → 單一 pooled 向量（DL-019）：fp32 平均，輸出 float32[128]，不二值化。

    valid_mask（選填，shape=(n,) bool）：False 的列（padding/特殊 token）不進平均。
    Stage A 用 cosine 距離（對縮放不敏感），故 pool 後不需 re-normalize；
    float16（halfvec）量化**只發生在 DB 綁定/寫入層**，不在此處——query 端
    提早量化會無謂損失精度（Codex 審查 HIGH-1）。
    離線建庫端與 query 端 MUST 共用本函式（§2.4 同一來源原則）。
    """
    arr = np.asarray(patch_embs, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != VECTOR_DIM:
        raise ValueError(f"pool_patches 期望 (n, {VECTOR_DIM}) 矩陣，收到 {arr.shape}")
    if valid_mask is not None:
        mask = np.asarray(valid_mask, dtype=bool)
        if mask.shape != (arr.shape[0],):
            raise ValueError(f"valid_mask 長度 {mask.shape} 與 patch 數 {arr.shape[0]} 不符")
        arr = arr[mask]
    if arr.shape[0] == 0:
        raise ValueError("沒有有效 patch 可池化（全部被 valid_mask 排除或輸入為空）")
    return arr.mean(axis=0)


def hamming_distance(a: bytes, b: bytes) -> int:
    """兩個等長 bit 串（bytes）的 Hamming 距離；§4.4 `<~>` 的純 Python 對照。"""
    if len(a) != len(b):
        raise ValueError(f"長度不一致：{len(a)} vs {len(b)} bytes")
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).bit_count()


def pooled_to_halfvec_literal(vec: "np.ndarray") -> str:
    """float32[128] → PostgreSQL halfvec 文字字面值 '[v1,v2,…]'（離線寫入與 query 端共用）。

    用 repr 保留 float 精度（halfvec 入庫/比對時 PG 端再量化為 fp16）。位序無關，
    與 binarize 不同——非檢索精度紅線，但集中於此處避免兩端格式漂移。
    """
    arr = np.asarray(vec, dtype=np.float32).ravel()
    if arr.shape[0] != VECTOR_DIM:
        raise ValueError(f"pooled 必須為 {VECTOR_DIM} 維，收到 {arr.shape[0]}")
    return "[" + ",".join(repr(float(x)) for x in arr) + "]"
