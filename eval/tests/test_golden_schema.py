"""golden_qa.seed.jsonl 與 load_golden 的 schema 驗證（§7.2）。"""
import json
from pathlib import Path

import pytest
from anatomy_eval.golden import ALLOWED_CATEGORIES, GoldenQA, load_golden

SEED = Path(__file__).resolve().parents[2] / "tests" / "golden_qa.seed.jsonl"


def test_seed_file_loads_and_covers_all_classes():
    items = load_golden(SEED)
    assert len(items) >= 10
    by_cat: dict[str, int] = {}
    for it in items:
        by_cat[it.category] = by_cat.get(it.category, 0) + 1
    assert set(by_cat) == ALLOWED_CATEGORIES        # 五類齊
    assert all(n >= 2 for n in by_cat.values())     # 每類 ≥2 題
    assert any("一" <= ch <= "鿿" for it in items for ch in it.query)  # 含中文 query（DL-013）


def test_non_oos_items_have_expected_pages_and_oos_dont():
    for it in load_golden(SEED):
        if it.category == "out_of_scope":
            assert it.expected_pages == () and it.expected_response_type == "教材中查無此項"
        else:
            assert len(it.expected_pages) >= 1 and it.expected_response_type is None


def test_load_golden_rejects_should_refuse(tmp_path):
    """§7.2：黃金題庫沒有 should_refuse 類別——出現即報錯，防止被偷加回。"""
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({
        "id": "x1", "category": "should_refuse", "query": "q",
    }) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="should_refuse"):
        load_golden(bad)


def test_load_golden_rejects_unknown_category_and_duplicate_id(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps({"id": "x1", "category": "nope", "query": "q"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="category"):
        load_golden(bad)
    dup = tmp_path / "dup.jsonl"
    row = {"id": "same", "category": "text_only", "query": "q", "expected_pages": ["a:1"]}
    dup.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重複"):
        load_golden(dup)


def test_load_golden_rejects_missing_required_and_unknown_fields(tmp_path):
    """Codex MEDIUM-7：缺必要欄位要報清楚錯誤（非 raw KeyError）；未知欄位視為拼字錯誤拒絕。"""
    missing = tmp_path / "missing.jsonl"
    missing.write_text(json.dumps({"category": "text_only", "query": "q",
                                   "expected_pages": ["a:1"]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="必要欄位"):
        load_golden(missing)
    typo = tmp_path / "typo.jsonl"
    typo.write_text(json.dumps({"id": "x", "category": "text_only", "query": "q",
                                "expected_page": ["a:1"]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="未知欄位"):
        load_golden(typo)


def test_goldenqa_is_frozen():
    it = GoldenQA(id="a", category="text_only", query="q", expected_pages=("a:1",))
    with pytest.raises(AttributeError):
        it.query = "changed"  # type: ignore[misc]
