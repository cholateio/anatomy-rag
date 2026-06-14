"""Tests for ragas_cli CLI (Task 2.3).

All marked @pytest.mark.ragas.
"""
import json
from pathlib import Path

import pytest
from anatomy_eval.ragas_cli import main

# ── helpers ────────────────────────────────────────────────────────────────────


SEED_GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "golden_qa.seed.jsonl"


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.ragas
def test_cli_mock_writes_report(tmp_path):
    """--mock run: report JSON written to --report path, exit 0.

    Uses the seed golden file (always present, < 110 → readiness warning printed
    to stderr but exit code is 0 — DL-028 not blocking).
    """
    report_path = tmp_path / "report.json"
    exit_code = main([
        "--golden", str(SEED_GOLDEN),
        "--report", str(report_path),
        "--mock",
    ])
    assert exit_code == 0, f"Expected exit 0, got {exit_code}"
    assert report_path.exists(), "report.json not written"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "report must be a JSON object"
    assert len(data) > 0, "report must have at least one metric"


@pytest.mark.ragas
def test_cli_mock_report_contains_deterministic_keys(tmp_path):
    """--mock report contains the expected deterministic metric keys."""
    report_path = tmp_path / "report.json"
    main([
        "--golden", str(SEED_GOLDEN),
        "--report", str(report_path),
        "--mock",
    ])
    data = json.loads(report_path.read_text(encoding="utf-8"))
    # deterministic_metrics() produces non_llm_context_recall, rouge_score(...),
    # and out_of_scope_correctness.
    assert "out_of_scope_correctness" in data
    assert any("rouge" in k.lower() for k in data), "Expected rouge_score key in report"
    assert any("context_recall" in k.lower() for k in data), "Expected context_recall key"


@pytest.mark.ragas
def test_cli_mock_all_scores_finite(tmp_path):
    """--mock report scores are all finite floats."""
    import math

    report_path = tmp_path / "report.json"
    main([
        "--golden", str(SEED_GOLDEN),
        "--report", str(report_path),
        "--mock",
    ])
    data = json.loads(report_path.read_text(encoding="utf-8"))
    for name, score in data.items():
        assert math.isfinite(score), f"Score for {name!r} is not finite: {score}"


@pytest.mark.ragas
def test_cli_mock_report_parent_dir_created(tmp_path):
    """--report path with non-existent parent directory is created automatically."""
    report_path = tmp_path / "subdir" / "nested" / "report.json"
    assert not report_path.parent.exists()
    exit_code = main([
        "--golden", str(SEED_GOLDEN),
        "--report", str(report_path),
        "--mock",
    ])
    assert exit_code == 0
    assert report_path.exists()


@pytest.mark.ragas
def test_cli_missing_golden_returns_error(tmp_path):
    """Non-existent --golden path exits with code 1."""
    exit_code = main([
        "--golden", str(tmp_path / "does_not_exist.jsonl"),
        "--report", str(tmp_path / "report.json"),
        "--mock",
    ])
    assert exit_code == 1


@pytest.mark.ragas
def test_cli_requires_mock_or_real(tmp_path):
    """Neither --mock nor --real raises SystemExit (argparse error)."""
    with pytest.raises(SystemExit):
        main([
            "--golden", str(SEED_GOLDEN),
            "--report", str(tmp_path / "report.json"),
        ])


@pytest.mark.ragas
def test_cli_real_exits_1_if_golden_not_ready(tmp_path):
    """--real 在黃金題庫未就緒（< 110 題）時必須 exit 1（H-2）。

    seed 黃金題庫 (golden_qa.seed.jsonl) 題數遠少於 110，readiness["ready"] = False。
    --real 不應耗費 API token 繼續執行，必須提前 exit 1 並不寫出 report。
    --mock 維持 warning-only（不受此限）。
    """
    report_path = tmp_path / "report.json"
    exit_code = main([
        "--golden", str(SEED_GOLDEN),
        "--report", str(report_path),
        "--real",
    ])
    assert exit_code == 1, (
        f"Expected exit 1 for --real with non-ready golden (< 110 entries), "
        f"got {exit_code}"
    )
    # report.json を書いてはいけない（API 呼び出しに到達していないことの証明）
    assert not report_path.exists(), (
        "report.json must NOT be written when --real exits early due to golden not ready"
    )
