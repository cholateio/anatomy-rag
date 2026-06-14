"""Recall-by-class gate（NaN-safe；複用 harness.evaluate_recall_by_class；DL-013）。

只評估 4 個檢索類別（text_only/figure_id/cross_page/clinical_correlation）；
out_of_scope 題的正確性屬生成層 gate（out_of_scope_correctness，DL-028 M-5）。

fail 條件（任一成立即 fail）：
1. actual is None（harness 未回傳該類別，即無該類別題目）
2. not math.isfinite(actual)（NaN / +inf / -inf）[H-2]
3. actual < thr（低於門檻）

low_sample_warnings：n_by_class < min_samples_per_class 時加入警告（不影響 passed 結果）。
"""
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from anatomy_eval.golden import GoldenQA
from anatomy_eval.harness import evaluate_recall_by_class


@dataclass
class RecallGateResult:
    """recall gate 檢查結果。

    Attributes:
        passed: 所有有門檻的類別均達標（非 NaN/missing/inf）。
        failures: 未達標的 (class_name, actual_value_or_none, threshold) 三元組清單。
        low_sample_warnings: 樣本數不足的類別名稱集合（n < min_samples；警告不影響 passed）。
        report: evaluate_recall_by_class 的原始回傳值。
    """
    passed: bool
    failures: list[tuple[str, float | None, float]] = field(default_factory=list)
    low_sample_warnings: set[str] = field(default_factory=set)
    report: dict = field(default_factory=dict)


def check_recall(
    golden: Sequence[GoldenQA],
    retrieve_fn: Callable[[GoldenQA], Sequence[str]],
    k: int,
    thresholds_by_class: dict[str, float],
    min_samples_per_class: int = 10,
) -> RecallGateResult:
    """對黃金題庫跑 recall@K by class，並對照門檻做 NaN-safe 檢查。

    Args:
        golden: 黃金題庫（GoldenQA 序列）。
        retrieve_fn: 給 GoldenQA 回排序頁碼列表的函式（引擎中立）。
        k: recall@K 的 K 值。
        thresholds_by_class: 各類別的 recall 門檻（來自 eval_thresholds.yaml
            recall_at_k.by_class）。
        min_samples_per_class: 低於此數量時加入 low_sample_warnings（預設 10）。

    Returns:
        RecallGateResult with passed=True iff ALL threshold-defined classes meet their goals.
    """
    report = evaluate_recall_by_class(golden, retrieve_fn, k)
    by_class: dict[str, float] = report.get("by_class", {})
    n_by_class: dict[str, int] = report.get("n_by_class", {})

    failures: list[tuple[str, float | None, float]] = []
    low_sample_warnings: set[str] = set()

    for cls, thr in thresholds_by_class.items():
        actual: float | None = by_class.get(cls)
        # NaN-safe fail condition
        if actual is None or not math.isfinite(actual) or actual < thr:
            failures.append((cls, actual, thr))
        # low sample warning（不影響 passed）
        n = n_by_class.get(cls, 0)
        if n < min_samples_per_class:
            low_sample_warnings.add(cls)

    return RecallGateResult(
        passed=len(failures) == 0,
        failures=failures,
        low_sample_warnings=low_sample_warnings,
        report=report,
    )
