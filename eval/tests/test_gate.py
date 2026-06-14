"""gate.py NaN-safe RAGAS gate 測試（§7.3，DL-028 active 門檻）。"""
from anatomy_eval.gate import check_ragas

THR = {"faithfulness": 0.90, "context_precision": 0.85}


def test_pass():
    r = check_ragas({"faithfulness": 0.92, "context_precision": 0.86}, THR)
    assert r.passed


def test_fail_low():
    r = check_ragas({"faithfulness": 0.92, "context_precision": 0.80}, THR)
    assert not r.passed
    assert ("context_precision", 0.80, 0.85) in r.failures


def test_nan_is_failure():
    # [H-2] float("nan") < thr 為 False → NaN 若未特判會「通過」→ 必須明確拒絕
    r = check_ragas({"faithfulness": float("nan"), "context_precision": 0.9}, THR)
    assert not r.passed
    assert any(f[0] == "faithfulness" for f in r.failures)


def test_missing_is_failure():
    # report 缺少 faithfulness → 不得通過
    assert not check_ragas({"faithfulness": 0.92}, THR).passed


def test_inf_is_failure():
    # +inf 不是有效分數
    r = check_ragas({"faithfulness": float("inf"), "context_precision": 0.86}, THR)
    assert not r.passed


def test_exact_threshold_passes():
    # 等於門檻視為通過（>=）
    r = check_ragas({"faithfulness": 0.90, "context_precision": 0.85}, THR)
    assert r.passed


def test_gate_result_has_failures_list():
    r = check_ragas({}, THR)
    assert isinstance(r.failures, list) and len(r.failures) == 2
