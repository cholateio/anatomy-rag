"""shared.binarize 的契約測試（Phase 0 最小版）。

驗證重點：輸出長度、決定性、正負號對應、維度檢查。
跨語言一致性 / golden 測試留待 Phase 1（Task 0.15 於乾淨進程強制 torch-free）。
"""
import numpy as np
import pytest
from anatomy_shared.binary import VECTOR_DIM, binarize, to_pg_bits


def test_returns_16_bytes_for_128d():
    """128 維輸入應回傳 bit(128) = 16 bytes。"""
    out = binarize(np.random.default_rng(0).standard_normal(VECTOR_DIM))
    assert isinstance(out, bytes)
    assert len(out) == 16


def test_deterministic_same_input_same_bytes():
    """相同輸入必須產生完全相同的 bytes（離線端 / query 端一致性前提）。"""
    vec = np.random.default_rng(42).standard_normal(VECTOR_DIM)
    assert binarize(vec) == binarize(vec.copy())


def test_all_positive_maps_to_all_ones():
    """全正向量 → 每個 bit 皆為 1 → 16 個 0xFF。"""
    assert binarize(np.ones(VECTOR_DIM)) == b"\xff" * 16


def test_all_negative_maps_to_all_zeros():
    """全負向量 → 每個 bit 皆為 0 → 16 個 0x00。"""
    assert binarize(-np.ones(VECTOR_DIM)) == b"\x00" * 16


def test_zero_maps_to_zero_bit():
    """vec_i == 0 視為非正 → bit = 0（> 0 才為 1）。"""
    assert binarize(np.zeros(VECTOR_DIM)) == b"\x00" * 16


def test_wrong_dimension_raises_value_error():
    """非 128 維輸入應拋 ValueError，避免靜默產生錯誤長度的 bit 串。"""
    with pytest.raises(ValueError):
        binarize(np.ones(64))


def test_to_pg_bits_length_and_extremes():
    """16 bytes → 128 字元 '0'/'1' 字串（§4.4 SQL 綁定用；PostgreSQL 無 bytea→bit cast）。"""
    assert to_pg_bits(b"\xff" * 16) == "1" * 128
    assert to_pg_bits(b"\x00" * 16) == "0" * 128


def test_to_pg_bits_msb_first_matches_binarize():
    """位序必須與 binarize 的 np.packbits（MSB-first）一致：vec[0]>0 對應字串第 1 個字元。"""
    vec = np.full(VECTOR_DIM, -1.0)
    vec[0] = 1.0
    assert to_pg_bits(binarize(vec)) == "1" + "0" * 127


# --- pool_patches（DL-019：fp32 平均、輸出 float32；halfvec 量化只發生在 DB 綁定層）---


def test_pool_patches_shape_dtype_and_mean():
    """(n,128) → (128,) float32；值為逐維 fp32 平均（不得提早 f16 量化）。"""
    from anatomy_shared.binary import pool_patches

    patches = np.stack([np.full(VECTOR_DIM, 1.0), np.full(VECTOR_DIM, 3.0)])
    pooled = pool_patches(patches)
    assert pooled.shape == (VECTOR_DIM,) and pooled.dtype == np.float32
    assert np.allclose(pooled, 2.0)


def test_pool_patches_accumulates_in_fp32():
    """float16 輸入也必須以 fp32 累加：±fp16max 與 2.0 的平均應為有限值 ≈ 0.667。"""
    from anatomy_shared.binary import pool_patches

    big = np.float16(65504.0)  # fp16 最大值；fp16 直接相加會溢位成 inf
    patches = np.stack([
        np.full(VECTOR_DIM, big, dtype=np.float16),
        np.full(VECTOR_DIM, -big, dtype=np.float16),
        np.full(VECTOR_DIM, 2.0, dtype=np.float16),
    ])
    pooled = pool_patches(patches)
    assert np.all(np.isfinite(pooled))
    assert np.allclose(pooled, 2.0 / 3.0, atol=1e-3)


def test_pool_patches_valid_mask_excludes_padding():
    """valid_mask=False 的列（padding/特殊 token）不得進入平均。"""
    from anatomy_shared.binary import pool_patches

    patches = np.stack([
        np.full(VECTOR_DIM, 1.0),
        np.full(VECTOR_DIM, 999.0),  # padding 列，應被排除
    ])
    pooled = pool_patches(patches, valid_mask=[True, False])
    assert np.allclose(pooled, 1.0)


def test_pool_patches_rejects_empty_and_bad_shape():
    from anatomy_shared.binary import pool_patches

    with pytest.raises(ValueError):
        pool_patches(np.ones((2, 64)))                      # 維度錯
    with pytest.raises(ValueError):
        pool_patches(np.ones((2, VECTOR_DIM)), valid_mask=[False, False])  # 全被遮罩
    with pytest.raises(ValueError):
        pool_patches(np.ones((2, VECTOR_DIM)), valid_mask=[True])          # mask 長度錯


def test_pool_patches_accepts_list_input():
    from anatomy_shared.binary import pool_patches

    pooled = pool_patches([[0.5] * VECTOR_DIM, [1.5] * VECTOR_DIM])
    assert np.allclose(pooled, 1.0)


# --- hamming_distance ---


def test_hamming_distance_identical_zero_and_complement_full():
    from anatomy_shared.binary import hamming_distance

    a = binarize(np.random.default_rng(7).standard_normal(VECTOR_DIM))
    assert hamming_distance(a, a) == 0
    flipped = bytes(b ^ 0xFF for b in a)
    assert hamming_distance(a, flipped) == 128


def test_hamming_distance_known_value():
    from anatomy_shared.binary import hamming_distance

    a = b"\x00" * 15 + b"\x0f"   # 末 4 bit 不同
    b = b"\x00" * 16
    assert hamming_distance(a, b) == 4


def test_hamming_distance_length_mismatch_raises():
    from anatomy_shared.binary import hamming_distance

    with pytest.raises(ValueError):
        hamming_distance(b"\x00" * 16, b"\x00" * 15)


def test_pooled_to_halfvec_literal_format():
    from anatomy_shared.binary import pooled_to_halfvec_literal
    import numpy as np
    lit = pooled_to_halfvec_literal(np.array([0.5, -0.25] + [0.0] * 126, dtype=np.float32))
    assert lit.startswith("[") and lit.endswith("]")
    parts = lit[1:-1].split(",")
    assert len(parts) == 128
    assert float(parts[0]) == 0.5 and float(parts[1]) == -0.25


def test_pooled_to_halfvec_literal_rejects_wrong_dim():
    import numpy as np
    import pytest
    from anatomy_shared.binary import pooled_to_halfvec_literal
    with pytest.raises(ValueError):
        pooled_to_halfvec_literal(np.zeros(64, dtype=np.float32))
