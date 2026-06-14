"""RAGAS NaN-safe gate（§7.3，DL-028 active 門檻）。

關鍵不變量：NaN/missing/+inf/-inf = FAIL。
`float("nan") < thr` 為 False（Python 浮點規則），所以不特判 NaN 就會「假綠通過」——
本實作明確用 `not math.isfinite(actual)` 攔截。

只檢查 active 門檻（`ragas.active`）；pending 門檻（如 context_recall）
由外部 CLI 僅印出，不影響 passed 結果（DL-028 [H-1]）。
"""
import math
from dataclasses import dataclass, field


@dataclass
class GateResult:
    """gate 檢查結果。

    Attributes:
        passed: 所有 active 指標均達門檻（非 NaN/missing/inf）。
        failures: 未達標的 (metric_name, actual_value_or_none, threshold) 三元組清單。
    """
    passed: bool
    failures: list[tuple[str, float | None, float]] = field(default_factory=list)


def check_ragas(
    report: dict[str, float | None],
    thresholds_active: dict[str, float],
) -> GateResult:
    """對 RAGAS report 的每個 active 門檻做 NaN-safe 檢查。

    fail 條件（任一成立即 fail）：
    1. actual is None（report 缺少該 metric）
    2. not math.isfinite(actual)（NaN / +inf / -inf）
    3. actual < thr（低於門檻）

    Args:
        report: RAGAS 評估結果 dict（key=metric_name, value=分數或 None）。
        thresholds_active: active 門檻 dict（來自 eval_thresholds.yaml ragas.active）。

    Returns:
        GateResult with passed=True iff ALL active metrics meet their thresholds.
    """
    failures: list[tuple[str, float | None, float]] = []
    for metric, thr in thresholds_active.items():
        actual = report.get(metric)
        if actual is None or not math.isfinite(actual) or actual < thr:
            failures.append((metric, actual, thr))
    return GateResult(passed=len(failures) == 0, failures=failures)
