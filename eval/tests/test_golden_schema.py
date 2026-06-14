"""golden_qa.seed.jsonl 與 load_golden 的 schema 驗證（§7.2）。"""
import json
from pathlib import Path

import pytest
from anatomy_eval.golden import (
    ALLOWED_CATEGORIES,
    CATEGORY_MINIMUMS,
    GoldenQA,
    golden_readiness,
    load_golden,
    parse_golden_row,
)

# ─── parse_golden_row + golden_readiness 新測試（Task 1.1）────────────────

def test_parse_golden_row_valid():
    qa = parse_golden_row(
        {"id": "x", "category": "text_only", "query": "q", "expected_pages": ["gray42:1"]}, 1
    )
    assert isinstance(qa, GoldenQA) and qa.id == "x"


def test_parse_golden_row_rejects_should_refuse():
    with pytest.raises(ValueError):
        parse_golden_row({"id": "x", "category": "should_refuse", "query": "q"}, 1)


def test_category_minimums():
    assert CATEGORY_MINIMUMS["text_only"] == 30 and sum(CATEGORY_MINIMUMS.values()) == 110


def test_golden_readiness_shortfall():
    items = [
        GoldenQA(id=f"t{i}", category="text_only", query="q", expected_pages=("gray42:1",))
        for i in range(3)
    ]
    rep = golden_readiness(items)
    assert rep["ready"] is False
    assert rep["shortfall"]["text_only"] == 27
    assert rep["shortfall"]["out_of_scope"] == 10

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


def test_goldenqa_is_unhashable_consistently():
    """__hash__=None：不論 metadata_filter 是 None 或 dict，hash 一律 TypeError（不留地雷）。"""
    a = GoldenQA(id="a", category="text_only", query="q", expected_pages=("a:1",))
    b = GoldenQA(id="b", category="text_only", query="q", expected_pages=("a:1",),
                 metadata_filter={"k": "v"})
    for item in (a, b):
        with pytest.raises(TypeError):
            hash(item)
    # 值相等仍保留
    assert a == GoldenQA(id="a", category="text_only", query="q", expected_pages=("a:1",))


def test_load_golden_rejects_string_pages_and_bad_types(tmp_path):
    """expected_pages 給裸字串（忘了方括號）必須報錯，不得拆成單字元 tuple。"""
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({"id": "x", "category": "text_only", "query": "q",
                             "expected_pages": "gray42:812"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="字串清單"):
        load_golden(f)
    bad_row = {"id": "x", "category": "text_only", "query": "q",
               "expected_pages": ["a:1"], "metadata_filter": "musculoskeletal"}
    f.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="metadata_filter"):
        load_golden(f)


def test_load_golden_reports_file_lineno_for_malformed_json(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text('{"id": "ok1", "category": "out_of_scope", "query": "q", '
                 '"expected_response_type": "教材中查無此項"}\n{not json}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_golden(f)


def test_load_golden_rejects_oos_with_pages_and_wrong_response_type(tmp_path):
    f = tmp_path / "oos.jsonl"
    f.write_text(json.dumps({"id": "o1", "category": "out_of_scope", "query": "q",
                             "expected_pages": ["a:1"],
                             "expected_response_type": "教材中查無此項"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="out_of_scope"):
        load_golden(f)
    f.write_text(json.dumps({"id": "o2", "category": "out_of_scope", "query": "q",
                             "expected_response_type": "找不到"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="教材中查無此項"):
        load_golden(f)


def test_load_golden_rejects_non_object_json_lines(tmp_path):
    """合法 JSON 但根非物件（裸字串/陣列）→ 受控 ValueError 帶行號，不得 AttributeError。"""
    f = tmp_path / "bad.jsonl"
    f.write_text('"just a string"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_golden(f)
    f.write_text('[1, 2]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 物件"):
        load_golden(f)


def test_load_golden_rejects_explicit_null_list_fields(tmp_path):
    """expected_pages/expected_concepts 顯式 null → 受控 ValueError，不得 TypeError。"""
    f = tmp_path / "null.jsonl"
    f.write_text(json.dumps({"id": "x", "category": "text_only", "query": "q",
                             "expected_pages": None}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="null"):
        load_golden(f)
