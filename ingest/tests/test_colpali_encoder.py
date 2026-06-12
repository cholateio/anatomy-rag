# ingest/tests/test_colpali_encoder.py
import numpy as np
from anatomy_ingest.colpali_encoder import encode_page_image
from anatomy_ingest.types import EncodedPage
from anatomy_shared.binary import binarize, pool_patches
from anatomy_shared.colpali_runtime import get_runtime


def test_encode_page_uses_shared_binarize_and_pool():
    runtime = get_runtime(mock=True)
    key = "page-key-A"  # mock encode_page 接受 str 鍵
    enc = encode_page_image(runtime, key)
    assert isinstance(enc, EncodedPage)
    assert enc.embed_model == "mock-colpali"
    # mock page = 64 patch、全 valid
    assert enc.n_patches == 64
    # 與直接用 shared 函式一致（單一來源驗證）
    vecs = runtime.encode_page(key)
    expected_bins = [binarize(v) for v in vecs.embeddings]
    expected_pooled = pool_patches(vecs.embeddings, vecs.valid_mask)
    assert enc.patch_bins == expected_bins
    np.testing.assert_array_equal(enc.pooled_f32, expected_pooled)


def test_encode_page_excludes_invalid_mask_from_pool_and_bins():
    # 構造一個 valid_mask 有 False 的假 runtime
    class FakeVecs:
        embeddings = np.random.default_rng(0).standard_normal((10, 128)).astype("float32")
        valid_mask = np.array([True] * 8 + [False] * 2)

    class FakeRuntime:
        model_id = "fake"

        def encode_page(self, image):
            return FakeVecs()

    enc = encode_page_image(FakeRuntime(), "x")
    assert enc.n_patches == 8  # 2 個 invalid 被排除
    assert enc.embed_model == "fake"
    # POOLED 路徑也必須排除 invalid 列——只平均前 8 列
    expected_pooled = FakeVecs.embeddings[:8].mean(axis=0)
    np.testing.assert_array_equal(enc.pooled_f32, expected_pooled)
    # 若回歸把 mask 從 pooling 拿掉（平均含 padding 列），下面這行會抓到
    assert not np.allclose(enc.pooled_f32, FakeVecs.embeddings.mean(axis=0))


def test_encode_page_deterministic():
    r = get_runtime(mock=True)
    assert encode_page_image(r, "same").patch_bins == encode_page_image(r, "same").patch_bins
