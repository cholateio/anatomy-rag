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
