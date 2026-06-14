"""RAGAS NaN-safe gate（§7.3，DL-028 active 門檻）。

關鍵不變量：NaN/missing/+inf/-inf = FAIL。
`float("nan") < thr` 為 False（Python 浮點規則），所以不特判 NaN 就會「假綠通過」——
本實作明確用 `not math.isfinite(actual)` 攔截。

只檢查 active 門檻（`ragas.active`）；pending 門檻（如 context_recall）
由外部 CLI 僅印出，不影響 passed 結果（DL-028 [H-1]）。

CLI 用法（真實 RAGAS gate workflow_dispatch job 使用）::

    python -m anatomy_eval.gate \\
        --report eval_report.json \\
        --thresholds eval/eval_thresholds.yaml
"""
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path


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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``python -m anatomy_eval.gate``.

    真實 RAGAS gate 使用（workflow_dispatch only, DL-028）。
    讀取 JSON report 與 eval_thresholds.yaml；只檢查 ragas.active 門檻；
    pending 門檻僅印出不阻止。

    Exit code: 0=通過，1=未達標或錯誤。
    """
    import argparse
    import json

    import yaml  # PyYAML（基底依賴，已在 pyproject.toml）

    parser = argparse.ArgumentParser(
        prog="python -m anatomy_eval.gate",
        description="NaN-safe RAGAS gate check（§7.3）",
    )
    parser.add_argument("--report", required=True, metavar="PATH", help="JSON report 路徑")
    parser.add_argument(
        "--thresholds", required=True, metavar="PATH", help="eval_thresholds.yaml 路徑"
    )
    args = parser.parse_args(argv)

    try:
        report: dict = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[gate] ERROR 讀取 report：{exc}", file=sys.stderr)
        return 1

    try:
        raw = yaml.safe_load(Path(args.thresholds).read_text(encoding="utf-8"))
    except (FileNotFoundError, Exception) as exc:
        print(f"[gate] ERROR 讀取 thresholds：{exc}", file=sys.stderr)
        return 1

    ragas_cfg = raw.get("ragas", {})
    active: dict[str, float] = ragas_cfg.get("active", {})
    pending: dict[str, float] = ragas_cfg.get("pending", {})

    # pending 僅印出，不計入 gate（DL-028 [H-1]）
    if pending:
        print("[gate] pending 指標（僅報告，不阻 merge）：")
        for m, thr in pending.items():
            actual_val = report.get(m)
            print(f"  {m}: actual={actual_val}, pending_threshold={thr}")

    result = check_ragas(report, active)

    if result.passed:
        print("[gate] RAGAS gate PASSED ✓")
        return 0

    print("[gate] RAGAS gate FAILED ✗", file=sys.stderr)
    for m, actual_val, thr in result.failures:
        print(f"  {m}: actual={actual_val!r} < threshold={thr}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
