"""MockColPaliRuntime 契約測試：形狀、valid_mask、決定性、torch-free（D-L）。"""
import importlib.util
import subprocess
import sys

import numpy as np
import pytest
from anatomy_shared.colpali_runtime import EncodedVectors, MockColPaliRuntime, get_runtime


def test_fresh_import_is_torch_free():
    """以 fresh subprocess 驗證 import 不拉 torch（D-L）；
    避免同進程中其他測試/插件已載入 torch 造成誤判（Codex MEDIUM-6）。"""
    code = "import anatomy_shared.colpali_runtime, sys; assert 'torch' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_mock_encode_query_shape_mask_and_determinism():
    rt = MockColPaliRuntime()
    enc1 = rt.encode_query("肱二頭肌的起止點")
    enc2 = rt.encode_query("肱二頭肌的起止點")
    assert isinstance(enc1, EncodedVectors)
    assert enc1.embeddings.shape == (rt.n_query_tokens, 128)
    assert enc1.embeddings.dtype == np.float32
    assert enc1.valid_mask.shape == (rt.n_query_tokens,) and enc1.valid_mask.dtype == np.bool_
    # mock 固定把前 2 個位置標為特殊/前綴 token（False）——演練「池化排除前綴」
    assert not enc1.valid_mask[0] and not enc1.valid_mask[1]
    assert enc1.valid_mask.sum() == rt.n_query_tokens - rt.n_special_prefix
    assert np.array_equal(enc1.embeddings, enc2.embeddings)
    assert not np.array_equal(enc1.embeddings, rt.encode_query("另一個問題").embeddings)


def test_mock_encode_page_accepts_str_key_and_array_image():
    rt = MockColPaliRuntime()
    p1 = rt.encode_page("gray42:812")
    p2 = rt.encode_page("gray42:812")
    assert p1.embeddings.shape == (rt.n_page_patches, 128) and p1.embeddings.dtype == np.float32
    assert bool(p1.valid_mask.all())                  # 頁面 patch 全有效
    assert np.array_equal(p1.embeddings, p2.embeddings)
    arr_img = np.zeros((4, 4, 3), dtype=np.uint8)     # array-like 影像（真實版為 PIL）
    a1, a2 = rt.encode_page(arr_img), rt.encode_page(arr_img)
    assert np.array_equal(a1.embeddings, a2.embeddings)


def test_mock_encode_page_different_keys_produce_different_embeddings():
    rt = MockColPaliRuntime()
    assert not np.array_equal(
        rt.encode_page("gray42:812").embeddings,
        rt.encode_page("gray42:999").embeddings,
    )


def test_get_runtime_mock_returns_mock_instance():
    rt = get_runtime(mock=True)
    assert isinstance(rt, MockColPaliRuntime)


@pytest.mark.skipif(
    importlib.util.find_spec("torch") is not None,
    reason="僅在無 torch 環境驗證錯誤訊息",
)
def test_get_runtime_real_raises_runtime_error_without_torch():
    """無 torch 安裝時，get_runtime(mock=False) 應提示安裝 gpu extra（RuntimeError）。"""
    with pytest.raises(RuntimeError, match="gpu"):
        get_runtime(mock=False)


def test_encoded_vectors_eq_is_identity_not_value():
    """eq=False：== 走 identity（不會對 ndarray 做 ambiguous 真值判斷而爆炸）。"""
    rt = MockColPaliRuntime()
    e1, e2 = rt.encode_query("q"), rt.encode_query("q")
    assert (e1 == e2) is False     # 不同實例
    assert (e1 == e1) is True      # 同一實例
