"""真實 ColPali runtime（gpu marker）：需 CUDA + gpu extra；CI 自動 skip。
手動（GPU 容器內）：見 Makefile encoder-gate / SETUP.md。"""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not torch.cuda.is_available(), reason="需 CUDA"),
]


@pytest.fixture(scope="module")
def runtime():
    from anatomy_shared.colpali_runtime import get_runtime

    return get_runtime(mock=False)   # 載入即執行 loading_info 守門


def test_query_shapes_and_mask(runtime):
    enc = runtime.encode_query("origin and insertion of biceps brachii")
    assert enc.embeddings.ndim == 2 and enc.embeddings.shape[1] == 128
    assert enc.embeddings.dtype == np.float32
    assert enc.valid_mask.dtype == bool and enc.valid_mask.shape == (enc.embeddings.shape[0],)
    assert enc.valid_mask.sum() >= 4     # 至少含實際 query tokens


def test_page_encode_smoke(runtime):
    from PIL import Image

    img = Image.new("RGB", (448, 448), "white")
    enc = runtime.encode_page(img)
    assert enc.embeddings.shape[1] == 128 and enc.valid_mask.all()  # 影像 patch 無 padding
    assert enc.embeddings.shape[0] >= 256   # 約 1024 patch（依 processor 解析度）


def test_same_query_twice_same_shape_close_values(runtime):
    a = runtime.encode_query("median nerve")
    b = runtime.encode_query("median nerve")
    assert a.embeddings.shape == b.embeddings.shape
    # eval+no_grad 下同輸入應幾乎一致（bf16 → fp32 容差）
    assert np.allclose(a.embeddings, b.embeddings, atol=5e-2)


def test_torch_and_transformers_are_validated_versions():
    """torch/transformers 必須是本 phase 驗證過的組合。
    lock 升版（如 transformers 5.11）會讓本測試紅 → 強制重跑 GPU 驗證後才能更新斷言。"""
    import transformers

    assert torch.__version__.startswith("2.11"), torch.__version__
    assert torch.version.cuda and torch.version.cuda.startswith("12.8"), torch.version.cuda
    assert transformers.__version__.startswith("5.10"), transformers.__version__


def test_different_query_lengths_yield_different_valid_counts(runtime):
    """valid 數隨 query 長度變動（mask 不是壞掉的常數）。"""
    short = runtime.encode_query("median nerve")
    long = runtime.encode_query(
        "course and branches of the median nerve in the forearm and the hand")
    assert long.valid_mask.sum() > short.valid_mask.sum()


def test_valid_rows_are_finite_and_nonzero(runtime):
    enc = runtime.encode_query("brachial plexus")
    valid = enc.embeddings[enc.valid_mask]
    assert np.isfinite(valid).all()
    assert (np.linalg.norm(valid, axis=1) > 0).all()
