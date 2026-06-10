"""maxsim_hamming 參考實作測試——§4.4 score(page)=Σ_t max_p (128 - hamming)。"""
import numpy as np
from anatomy_eval.reference import maxsim_hamming
from anatomy_shared.binary import VECTOR_DIM, binarize


def test_maxsim_identical_tokens_score_full():
    """query token 與某 patch 完全相同 → 該 token 貢獻滿分 128。"""
    rng = np.random.default_rng(1)
    patch_vecs = [rng.standard_normal(VECTOR_DIM) for _ in range(4)]
    patches = [binarize(v) for v in patch_vecs]
    tokens = [patches[0], patches[2]]  # 直接取兩個 patch 當 token
    assert maxsim_hamming(tokens, patches) == 128.0 * 2


def test_maxsim_hand_computed_small_case():
    """2 token × 2 patch 手算對照。"""
    t1 = b"\x00" * 16                    # 與 p1 距離 0、與 p2 距離 4
    t2 = b"\xff" + b"\x00" * 15          # 與 p1 距離 8、與 p2 距離 12
    p1 = b"\x00" * 16
    p2 = b"\x00" * 15 + b"\x0f"
    # token1 max sim = 128-0；token2 max sim = 128-8
    assert maxsim_hamming([t1, t2], [p1, p2]) == (128.0 - 0) + (128.0 - 8)


def test_maxsim_orders_relevant_page_first():
    """query 取自 page A 的 patch → A 的分數必須高於無關的 page B。"""
    rng = np.random.default_rng(2)
    page_a = [binarize(rng.standard_normal(VECTOR_DIM)) for _ in range(8)]
    page_b = [binarize(rng.standard_normal(VECTOR_DIM)) for _ in range(8)]
    tokens = page_a[:3]
    assert maxsim_hamming(tokens, page_a) > maxsim_hamming(tokens, page_b)


def test_maxsim_rejects_non_16_byte_inputs():
    """128-distance 的假設只對 bit(128)=16 bytes 成立——其他長度必須報錯（Codex LOW-8）。"""
    import pytest

    with pytest.raises(ValueError):
        maxsim_hamming([b"\x00" * 8], [b"\x00" * 8])
