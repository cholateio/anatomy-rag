"""Tests for review_loader.py (Task 5.1 — §7.4 抽檢工具資料層)."""
import json
from pathlib import Path

import pytest


def test_sample_logs_deterministic():
    """同一 seed 回傳相同結果（決定性）。"""
    from anatomy_eval.review_loader import sample_logs

    rows = [{"query": f"q{i}"} for i in range(20)]
    s1 = sample_logs(rows, 5, seed=42)
    s2 = sample_logs(rows, 5, seed=42)
    assert s1 == s2
    assert len(s1) == 5


def test_sample_logs_different_seeds_differ():
    """不同 seed 結果不同（機率性驗證）。"""
    from anatomy_eval.review_loader import sample_logs

    rows = [{"query": f"q{i}"} for i in range(20)]
    s1 = sample_logs(rows, 10, seed=1)
    s2 = sample_logs(rows, 10, seed=99)
    # 20 取 10，相同的機率 ≈ 1/C(20,10) < 0.007%，視為不可能相同
    assert s1 != s2


def test_sample_logs_clinical_first():
    """clinical_flavored=True 的列在前；非臨床列在後。"""
    from anatomy_eval.review_loader import sample_logs

    clinical = [{"query": f"clin{i}", "clinical_flavored": True} for i in range(5)]
    non_clinical = [{"query": f"reg{i}", "clinical_flavored": False} for i in range(10)]
    rows = non_clinical + clinical  # 打亂順序
    result = sample_logs(rows, 8, seed=0)
    # clinical 必須全部排在 non-clinical 之前
    saw_non_clinical = False
    for row in result:
        if not row.get("clinical_flavored"):
            saw_non_clinical = True
        elif saw_non_clinical:
            pytest.fail("clinical_flavored 列出現在 non-clinical 之後（違反優先排前規則）")


def test_sample_logs_n_ge_rows_returns_all():
    """n >= len(rows) 時回傳全部（clinical 優先排序後）。"""
    from anatomy_eval.review_loader import sample_logs

    rows = [{"query": "q"}] * 3
    result = sample_logs(rows, 10, seed=42)
    assert len(result) == 3


def test_sample_logs_empty():
    """空輸入回傳空列表。"""
    from anatomy_eval.review_loader import sample_logs

    assert sample_logs([], 5, seed=0) == []


def test_export_annotations_writes_jsonl(tmp_path: Path):
    """export_annotations 寫出有效 JSONL，每列有 label/comment。"""
    from anatomy_eval.review_loader import export_annotations

    annotations = [
        {"query": "q1", "label": "correct", "comment": "看起來不錯"},
        {"query": "q2", "label": "wrong", "comment": ""},
        {"query": "q3", "label": "partial", "comment": "部分正確"},
    ]
    out = tmp_path / "out.jsonl"
    export_annotations(annotations, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["label"] == "correct"
    assert json.loads(lines[1])["label"] == "wrong"
    assert json.loads(lines[2])["label"] == "partial"


def test_export_annotations_bad_label_raises(tmp_path: Path):
    """非法 label 必須 raise ValueError，並不寫出任何檔案。"""
    from anatomy_eval.review_loader import export_annotations

    out = tmp_path / "out.jsonl"
    with pytest.raises(ValueError, match="excellent"):
        export_annotations([{"query": "q", "label": "excellent", "comment": ""}], out)
    assert not out.exists()


def test_export_annotations_none_label_raises(tmp_path: Path):
    """label=None 也必須 raise ValueError。"""
    from anatomy_eval.review_loader import export_annotations

    with pytest.raises(ValueError):
        export_annotations([{"query": "q", "label": None, "comment": ""}], tmp_path / "a.jsonl")


def test_export_annotations_creates_parent_dir(tmp_path: Path):
    """父目錄不存在時自動建立。"""
    from anatomy_eval.review_loader import export_annotations

    out = tmp_path / "sub" / "nested" / "out.jsonl"
    export_annotations([{"label": "correct", "comment": ""}], out)
    assert out.exists()


# ── to_golden_row (H-1) ──────────────────────────────────────────────────────


def test_to_golden_row_projects_away_log_fields():
    """to_golden_row 丟棄 label/comment/answer/sources 等日誌欄位（H-1）。"""
    from anatomy_eval.review_loader import to_golden_row

    annotation = {
        "id": "log-001",
        "category": "text_only",
        "query": "What is anatomy?",
        "expected_pages": ["gray42:1"],
        "answer": "Some LLM answer",
        "sources": ["page1", "page2"],
        "label": "wrong",
        "comment": "incorrect answer",
        "clinical_flavored": True,
    }
    result = to_golden_row(annotation)
    # Golden schema fields preserved
    assert result["id"] == "log-001"
    assert result["category"] == "text_only"
    assert result["query"] == "What is anatomy?"
    assert result["expected_pages"] == ["gray42:1"]
    # Log-only fields dropped
    assert "answer" not in result
    assert "sources" not in result
    assert "label" not in result
    assert "comment" not in result
    assert "clinical_flavored" not in result
    # Only golden schema fields present
    golden_fields = {"id", "category", "query", "expected_pages", "expected_concepts",
                     "metadata_filter", "expected_response_type"}
    assert set(result.keys()) <= golden_fields


def test_to_golden_row_result_passes_parse_golden_row():
    """to_golden_row 投影後的 dict 能通過 parse_golden_row 驗證（H-1 核心契約）。"""
    from anatomy_eval.golden import parse_golden_row
    from anatomy_eval.review_loader import to_golden_row

    annotation = {
        "id": "log-002",
        "category": "text_only",
        "query": "肱二頭肌起止點？",
        "expected_pages": ["gray42:1"],
        "answer": "Some answer",
        "label": "wrong",
        "comment": "needs improvement",
        "extra_field": "should be dropped",
    }
    projected = to_golden_row(annotation)
    qa = parse_golden_row(projected, 0)  # 未通過則 raise ValueError
    assert qa.id == "log-002"
    assert qa.category == "text_only"
    assert qa.query == "肱二頭肌起止點？"


def test_to_golden_row_missing_id_raises():
    """缺 id 時 raise ValueError（H-1：讓 review_tool 顯示明確錯誤）。"""
    from anatomy_eval.review_loader import to_golden_row

    with pytest.raises(ValueError, match="id"):
        to_golden_row({
            "category": "text_only",
            "query": "What is anatomy?",
            "expected_pages": ["gray42:1"],
            "label": "wrong",
        })


def test_to_golden_row_missing_category_raises():
    """缺 category 時 raise ValueError。"""
    from anatomy_eval.review_loader import to_golden_row

    with pytest.raises(ValueError, match="category"):
        to_golden_row({
            "id": "x1",
            "query": "What is anatomy?",
            "label": "wrong",
        })


def test_to_golden_row_oos_annotation():
    """OOS 標注（無 expected_pages）也能正確投影；完整驗證交給 parse_golden_row。"""
    from anatomy_eval.review_loader import to_golden_row

    annotation = {
        "id": "oos-001",
        "category": "out_of_scope",
        "query": "今天天氣如何？",
        "expected_response_type": "教材中查無此項",
        "answer": "教材中查無此項",
        "label": "wrong",
        "comment": "",
    }
    result = to_golden_row(annotation)
    assert result["expected_response_type"] == "教材中查無此項"
    assert "answer" not in result
    assert "label" not in result
