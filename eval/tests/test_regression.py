"""regression.py — promote_cases 測試（in-memory 驗證 + 原子附加）。"""
import pytest
from anatomy_eval.golden import load_golden
from anatomy_eval.regression import promote_cases


def _valid_case(uid: str, category: str = "text_only") -> dict:
    return {
        "id": uid,
        "category": category,
        "query": f"q for {uid}",
        "expected_pages": ["gray42:1"],
    }


def _oos_case(uid: str) -> dict:
    return {
        "id": uid,
        "category": "out_of_scope",
        "query": "outside scope",
        "expected_response_type": "教材中查無此項",
    }


def test_promote_cases_appends_valid(tmp_path):
    reg = tmp_path / "regression.jsonl"
    cases = [_valid_case("r1"), _valid_case("r2")]
    added = promote_cases(cases, reg)
    assert added == 2
    items = load_golden(reg)
    assert {it.id for it in items} == {"r1", "r2"}


def test_promote_cases_deduplicates_existing(tmp_path):
    reg = tmp_path / "regression.jsonl"
    # 先促進 r1
    promote_cases([_valid_case("r1")], reg)
    # 再次促進 r1 + r2
    added = promote_cases([_valid_case("r1"), _valid_case("r2")], reg)
    assert added == 1  # r1 已存在，只加 r2
    items = load_golden(reg)
    assert len(items) == 2


def test_promote_cases_deduplicates_within_batch(tmp_path):
    reg = tmp_path / "regression.jsonl"
    # batch 內重複
    added = promote_cases([_valid_case("r1"), _valid_case("r1")], reg)
    assert added == 1
    items = load_golden(reg)
    assert len(items) == 1


def test_promote_cases_rejects_invalid_schema(tmp_path):
    """非法 schema → parse_golden_row raise → promote_cases 不寫入、自行 propagate。"""
    reg = tmp_path / "regression.jsonl"
    bad = {"id": "bad", "category": "should_refuse", "query": "q"}
    with pytest.raises(ValueError, match="should_refuse"):
        promote_cases([bad], reg)
    # 檔案不應建立或為空
    if reg.exists():
        assert reg.stat().st_size == 0 or load_golden(reg) == []


def test_promote_cases_rejects_missing_required_field(tmp_path):
    reg = tmp_path / "regression.jsonl"
    bad = {"category": "text_only", "query": "q", "expected_pages": ["a:1"]}  # 缺 id
    with pytest.raises(ValueError, match="必要欄位"):
        promote_cases([bad], reg)


def test_promote_cases_returns_zero_for_all_existing(tmp_path):
    reg = tmp_path / "regression.jsonl"
    promote_cases([_valid_case("r1")], reg)
    added = promote_cases([_valid_case("r1")], reg)
    assert added == 0


def test_promote_cases_creates_parent_directory(tmp_path):
    reg = tmp_path / "nested" / "sub" / "regression.jsonl"
    added = promote_cases([_valid_case("r1")], reg)
    assert added == 1
    assert reg.exists()


def test_promote_cases_validates_in_memory_not_tmp_file(tmp_path, monkeypatch):
    """[M-3] 驗證路徑：parse_golden_row 在記憶體中呼叫，不寫任何 tmp 檔。"""
    reg = tmp_path / "regression.jsonl"
    # monkeypatch Path.write_text 確認沒有寫 tmp 路徑
    written_paths: list = []
    original_write = type(reg).write_text

    def tracking_write(self, *args, **kwargs):
        written_paths.append(str(self))
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(type(reg), "write_text", tracking_write)

    # 有效 case 應只寫 regression.jsonl 本身（open("a") 模式，write_text 不被呼叫）
    promote_cases([_valid_case("r1")], reg)
    # 確認沒有寫 tmp 路徑（如 /tmp/* 或含 "tmp" 的路徑）
    assert not any("tmp" in p.lower() and str(reg) not in p for p in written_paths)


def test_promote_cases_oos_valid(tmp_path):
    reg = tmp_path / "regression.jsonl"
    added = promote_cases([_oos_case("oos1")], reg)
    assert added == 1
    items = load_golden(reg)
    assert items[0].category == "out_of_scope"
