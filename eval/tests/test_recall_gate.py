"""recall_by_class gate 測試（NaN-safe；複用 harness）。"""
from anatomy_eval.golden import GoldenQA
from anatomy_eval.recall_by_class import check_recall


def _mk(qid, cat, query, pages):
    return GoldenQA(id=qid, category=cat, query=query, expected_pages=tuple(pages))


THRESHOLDS = {"text_only": 0.80, "figure_id": 0.75, "cross_page": 0.70}
MIN_SAMPLES = 3


def _make_golden():
    return [
        _mk("t1", "text_only", "q", ["p1"]),
        _mk("t2", "text_only", "q", ["p1"]),
        _mk("t3", "text_only", "q", ["p1"]),
        _mk("f1", "figure_id", "q", ["p9"]),
        _mk("f2", "figure_id", "q", ["p9"]),
        _mk("f3", "figure_id", "q", ["p9"]),
        _mk("c1", "cross_page", "q", ["p2"]),
        _mk("c2", "cross_page", "q", ["p2"]),
        _mk("c3", "cross_page", "q", ["p2"]),
    ]


def test_pass_all_classes():
    # 全部命中
    r = check_recall(
        _make_golden(),
        retrieve_fn=lambda qa: list(qa.expected_pages) + ["extra"],
        k=5,
        thresholds_by_class=THRESHOLDS,
        min_samples_per_class=MIN_SAMPLES,
    )
    assert r.passed
    assert r.failures == []


def test_fail_low_class():
    # figure_id 完全不命中 → 低於門檻
    golden = _make_golden()
    r = check_recall(
        golden,
        retrieve_fn=lambda qa: [] if qa.category == "figure_id" else list(qa.expected_pages),
        k=5,
        thresholds_by_class=THRESHOLDS,
        min_samples_per_class=MIN_SAMPLES,
    )
    assert not r.passed
    assert any(f[0] == "figure_id" for f in r.failures)


def test_low_sample_warning():
    # cross_page 只有 1 題 < min_samples → low_sample_warnings，但不 fail（僅警告）
    golden = [
        _mk("t1", "text_only", "q", ["p1"]),
        _mk("t2", "text_only", "q", ["p1"]),
        _mk("t3", "text_only", "q", ["p1"]),
        _mk("f1", "figure_id", "q", ["p9"]),
        _mk("f2", "figure_id", "q", ["p9"]),
        _mk("f3", "figure_id", "q", ["p9"]),
        _mk("c1", "cross_page", "q", ["p2"]),  # 只有 1 題
    ]
    r = check_recall(
        golden,
        retrieve_fn=lambda qa: list(qa.expected_pages),
        k=5,
        thresholds_by_class=THRESHOLDS,
        min_samples_per_class=MIN_SAMPLES,
    )
    assert "cross_page" in r.low_sample_warnings


def test_nan_is_failure():
    """NaN recall 不得通過 gate（[H-2]）。"""
    from unittest.mock import patch

    # 直接注入帶 NaN 的 by_class report
    from anatomy_eval import recall_by_class as rcm

    fake_report = {
        "k": 5,
        "n_evaluated": 3,
        "n_skipped_oos": 0,
        "by_class": {"text_only": float("nan"), "figure_id": 0.80, "cross_page": 0.70},
        "n_by_class": {"text_only": 3, "figure_id": 3, "cross_page": 3},
        "overall": 0.5,
    }
    with patch.object(rcm, "evaluate_recall_by_class", return_value=fake_report):
        r = check_recall(
            _make_golden(),
            retrieve_fn=lambda qa: [],
            k=5,
            thresholds_by_class=THRESHOLDS,
            min_samples_per_class=MIN_SAMPLES,
        )
    assert not r.passed
    assert any(f[0] == "text_only" for f in r.failures)


def test_missing_class_is_failure():
    """gate 門檻列出的類別若 harness 未回傳（無題目）→ fail（missing = None）。"""
    golden = [
        _mk("t1", "text_only", "q", ["p1"]),
    ]
    r = check_recall(
        golden,
        retrieve_fn=lambda qa: list(qa.expected_pages),
        k=5,
        thresholds_by_class=THRESHOLDS,
        min_samples_per_class=1,
    )
    # figure_id / cross_page 完全沒題目 → by_class 無對應 key → fail
    assert not r.passed
    missing = {f[0] for f in r.failures}
    assert "figure_id" in missing or "cross_page" in missing
