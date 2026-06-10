"""黃金題庫載入與 schema 驗證（§7.2）。

紅線：黃金題庫**沒有** `should_refuse` 類別（出現即 ValueError）；
`out_of_scope` 測「教材中查無此項」，不帶 expected_pages、不計 retrieval recall。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_CATEGORIES = {
    "text_only", "figure_id", "cross_page", "clinical_correlation", "out_of_scope",
}

_KNOWN_FIELDS = {"id", "category", "query", "expected_pages", "expected_concepts",
                 "metadata_filter", "expected_response_type"}


@dataclass(frozen=True)
class GoldenQA:
    id: str
    category: str
    query: str
    expected_pages: tuple[str, ...] = ()
    expected_concepts: tuple[str, ...] = ()
    metadata_filter: dict | None = field(default=None)
    expected_response_type: str | None = None

    # 顯式不可雜湊：metadata_filter 為 dict（unhashable）；與其「None 可 hash、dict 爆炸」
    # 的不一致行為，不如一律禁止（需要去重請用 item.id）。__eq__ 仍為值比較。
    __hash__ = None  # type: ignore[assignment]


def load_golden(path: str | Path) -> list[GoldenQA]:
    items: list[GoldenQA] = []
    seen_ids: set[str] = set()
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {lineno}: 無效 JSON——{e}") from e
        if not isinstance(raw, dict):
            raise ValueError(f"line {lineno}: 每行必須是 JSON 物件，收到 {type(raw).__name__}")
        cat = raw.get("category")
        if cat == "should_refuse":
            raise ValueError(f"line {lineno}: 黃金題庫不得有 should_refuse 類別（§7.2）")
        for req in ("id", "category", "query"):
            if not isinstance(raw.get(req), str) or not raw.get(req):
                raise ValueError(f"line {lineno}: 缺少或非字串的必要欄位 {req!r}")
        unknown = set(raw) - _KNOWN_FIELDS
        if unknown:
            raise ValueError(f"line {lineno}: 未知欄位 {sorted(unknown)}（疑似拼字錯誤）")
        if cat not in ALLOWED_CATEGORIES:
            raise ValueError(f"line {lineno}: 未知 category {cat!r}")
        if raw["id"] in seen_ids:
            raise ValueError(f"line {lineno}: 重複 id {raw['id']!r}")
        seen_ids.add(raw["id"])
        for field_name in ("expected_pages", "expected_concepts"):
            val = raw.get(field_name)
            if val is None and field_name in raw:
                raise ValueError(f"line {lineno}: {field_name} 不得為 null")
            if val is not None:
                if not isinstance(val, list) or not all(isinstance(s, str) and s for s in val):
                    raise ValueError(f"line {lineno}: {field_name} 必須是字串清單")
        mf = raw.get("metadata_filter")
        if mf is not None and not isinstance(mf, dict):
            raise ValueError(f"line {lineno}: metadata_filter 必須是物件或 null")
        item = GoldenQA(
            id=raw["id"],
            category=cat,
            query=raw["query"],
            expected_pages=tuple(raw.get("expected_pages", [])),
            expected_concepts=tuple(raw.get("expected_concepts", [])),
            metadata_filter=raw.get("metadata_filter"),
            expected_response_type=raw.get("expected_response_type"),
        )
        if cat == "out_of_scope":
            if item.expected_pages:
                raise ValueError(f"line {lineno}: out_of_scope 不得帶 expected_pages")
            if item.expected_response_type != "教材中查無此項":
                raise ValueError(
                    f"line {lineno}: out_of_scope 須 expected_response_type=教材中查無此項"
                )
        elif not item.expected_pages:
            raise ValueError(f"line {lineno}: {cat} 題必須有 expected_pages")
        items.append(item)
    return items
