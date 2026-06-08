"""shared.binarize 的契約測試（Phase 0 最小版）。

驗證重點：輸出長度、決定性、正負號對應、維度檢查。
跨語言一致性 / golden 測試留待 Phase 1（Task 0.15 於乾淨進程強制 torch-free）。
"""
import numpy as np
import pytest
from anatomy_shared.binary import VECTOR_DIM, binarize


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
