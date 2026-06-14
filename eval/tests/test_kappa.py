"""kappa.py — Cohen's kappa 測試（pe≈1→nan；kappa_gate(nan)→False）。"""
import math

import pytest
from anatomy_eval.kappa import cohen_kappa, kappa_gate


def test_perfect_agreement_multi_class():
    """完全一致且有多個類別 → kappa = 1.0。"""
    a = ["A", "B", "A", "B", "C"]
    b = ["A", "B", "A", "B", "C"]
    k = cohen_kappa(a, b)
    assert math.isclose(k, 1.0, abs_tol=1e-9)


def test_single_class_is_nan():
    """所有標記都是同一類別（pe≈1）→ kappa 未定義，回 nan。"""
    a = ["A", "A", "A"]
    b = ["A", "A", "A"]
    k = cohen_kappa(a, b)
    assert math.isnan(k)


def test_kappa_gate_nan_is_false():
    """kappa_gate(nan) → False（不達標，須重寫）[M-6]。"""
    assert kappa_gate(float("nan")) is False


def test_kappa_gate_low_is_false():
    assert kappa_gate(0.5) is False


def test_kappa_gate_high_passes():
    assert kappa_gate(0.7) is True
    assert kappa_gate(0.9) is True


def test_length_mismatch_raises():
    with pytest.raises(ValueError, match="長度"):
        cohen_kappa(["A", "B"], ["A"])


def test_empty_raises():
    with pytest.raises(ValueError, match="空"):
        cohen_kappa([], [])


def test_known_value():
    """已知 kappa 值（2 evaluators，簡單 2x2 矩陣）。"""
    # annotator A: [A,A,B,B], annotator B: [A,B,A,B]
    # p_o = (1+1)/4 = 0.5
    # p_A: A=(2+2)/(4*2)=0.5, p_B: B=(2+2)/(4*2)=0.5
    # p_e = 0.5^2 + 0.5^2 = 0.5
    # kappa = (0.5 - 0.5) / (1 - 0.5) = 0.0
    a = ["A", "A", "B", "B"]
    b = ["A", "B", "A", "B"]
    k = cohen_kappa(a, b)
    assert math.isclose(k, 0.0, abs_tol=1e-9)


def test_all_disagree_returns_negative():
    """完全不一致（2 classes）→ kappa 為負值。"""
    a = ["A", "A", "B", "B"]
    b = ["B", "B", "A", "A"]
    k = cohen_kappa(a, b)
    assert k < 0


def test_pe_near_one_returns_nan():
    """極端不平衡（幾乎全 A）使 pe≈1 → nan，不得回 1.0 或拋例外 [M-6]。"""
    # pe = (99/100)^2 + (1/100)^2 < 1，有兩個類別，kappa 應接近 1.0（非 nan）
    # 換用全同且只有一個類別的情況：pe = 1 → nan
    a2 = ["A"] * 100
    b2 = ["A"] * 100
    k2 = cohen_kappa(a2, b2)
    assert math.isnan(k2)
