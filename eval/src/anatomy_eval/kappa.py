"""Cohen's kappa 標注一致性（§7.4 multi-annotator；DL-028 [M-6]）。

pe ≈ 1（單一類別、無區辨）→ return float("nan")（未定義，非 1.0）。
kappa_gate(nan) → False（不達標，須重寫）。

kappa = (p_o - p_e) / (1 - p_e)
  p_o = observed agreement（實際一致率）
  p_e = expected agreement by chance（各類別邊際機率的平方和）
"""
import math
from collections import Counter


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """計算兩位標注者的 Cohen's kappa。

    Args:
        a: 標注者 A 的標籤序列。
        b: 標注者 B 的標籤序列。

    Returns:
        kappa 值（float）；pe ≈ 1（單一類別）→ float("nan")。

    Raises:
        ValueError: 長度不符或輸入為空。
    """
    if len(a) != len(b):
        raise ValueError(f"兩組標籤長度不符：{len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        raise ValueError("輸入標籤序列不得為空")

    # 實際一致率
    p_o = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n

    # 邊際機率（各標注者分別的類別分佈）
    count_a = Counter(a)
    count_b = Counter(b)
    all_labels = set(count_a) | set(count_b)

    p_e = sum(
        (count_a.get(label, 0) / n) * (count_b.get(label, 0) / n)
        for label in all_labels
    )

    # pe ≈ 1：單一類別或完全無區辨 → kappa 未定義
    if pe_near_one(p_e):
        return float("nan")

    return (p_o - p_e) / (1.0 - p_e)


def pe_near_one(p_e: float, tol: float = 1e-9) -> bool:
    """判斷 p_e 是否接近 1（無法計算 kappa）。"""
    return p_e >= 1.0 - tol


def kappa_gate(kappa: float, threshold: float = 0.7) -> bool:
    """kappa gate：≥ threshold 才達標（NaN → False）。

    Args:
        kappa: Cohen's kappa 值（可為 nan）。
        threshold: 達標門檻（預設 0.7）。

    Returns:
        True iff kappa is finite and >= threshold.
    """
    if math.isnan(kappa):
        return False
    return kappa >= threshold
