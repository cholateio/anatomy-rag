"""Streamlit 抽檢工具的資料層（§7.4）。

此模組為純 Python，不依賴 Streamlit，可獨立單元測試。
業務邏輯全在此；review_tool.py 為薄 Streamlit shell。

函式：
    sample_logs(rows, n, *, seed) — 決定性隨機抽樣，clinical_flavored 優先排前。
    export_annotations(annotations, path) — 匯出標注至 JSONL；label 非法即 raise。
    to_golden_row(annotation) -> dict — 標注 dict 投影至黃金 schema（H-1）。
"""
import json
import random
from pathlib import Path

# §7.4 合法標注標籤
VALID_LABELS: frozenset[str] = frozenset({"correct", "partial", "wrong"})

# H-1: 黃金 schema 接受的欄位（與 golden.py _KNOWN_FIELDS 一致）。
# 日誌列含有 label/comment/answer/sources 等多餘欄位，parse_golden_row 遇未知
# 欄位即 raise ValueError——必須在促進前投影至此集合。
_GOLDEN_SCHEMA_FIELDS: frozenset[str] = frozenset({
    "id", "category", "query", "expected_pages", "expected_concepts",
    "metadata_filter", "expected_response_type",
})
# 最少必須存在於投影結果中的欄位（parse_golden_row 的前三個必要欄位）。
_GOLDEN_REQUIRED_FIELDS: frozenset[str] = frozenset({"id", "category", "query"})


def sample_logs(rows: list[dict], n: int, *, seed: int) -> list[dict]:
    """從日誌列表中決定性隨機抽取 n 筆，clinical_flavored=True 的優先排前。

    Args:
        rows:  日誌列表（每筆為 dict，可含 clinical_flavored: bool 欄位）。
        n:     最多抽取筆數。
        seed:  隨機種子（確保決定性；random.Random(seed)）。

    Returns:
        最多 n 筆日誌；clinical_flavored=True 的列整批排在前面，
        兩組內部分別以同一 seed 決定性 shuffle。
        若 n >= len(rows)，回傳全部（同樣依 clinical 優先排序）。
    """
    rng = random.Random(seed)
    clinical = [r for r in rows if r.get("clinical_flavored")]
    non_clinical = [r for r in rows if not r.get("clinical_flavored")]
    rng.shuffle(clinical)
    rng.shuffle(non_clinical)
    ordered = clinical + non_clinical
    return ordered[:n] if n < len(ordered) else ordered


def export_annotations(annotations: list[dict], path: str | Path) -> None:
    """將標注結果匯出至 JSONL 檔案。

    Args:
        annotations: 標注列表，每筆須含 'label'（correct/partial/wrong）欄位。
        path:        輸出 JSONL 路徑（父目錄不存在時自動建立）。

    Raises:
        ValueError: 若任一筆的 'label' 不在 VALID_LABELS 內。
    """
    for i, ann in enumerate(annotations):
        label = ann.get("label")
        if label not in VALID_LABELS:
            raise ValueError(
                f"第 {i} 筆標注標籤非法：{label!r}（合法值：{sorted(VALID_LABELS)}）"
            )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for ann in annotations:
            f.write(json.dumps(ann, ensure_ascii=False) + "\n")


def to_golden_row(annotation: dict) -> dict:
    """標注 dict 投影至黃金 schema（H-1：防止 review_tool 促進時欄位衝突）。

    推理日誌列含有 label / comment / answer / sources 等 parse_golden_row 不接受的
    欄位；直接傳入 promote_cases → parse_golden_row 會因「未知欄位」而 raise。
    本函式先投影至黃金 schema 欄位集合，再交由 parse_golden_row 做完整 schema 驗證。

    Args:
        annotation: 標注 dict（含 label/comment 等日誌欄位，以及黃金欄位的子集）。

    Returns:
        僅含黃金 schema 欄位 ``{id, category, query, expected_pages,
        expected_concepts, metadata_filter, expected_response_type}`` 的 dict。

    Raises:
        ValueError: 若缺少 id、category 或 query（parse_golden_row 的必要欄位），
            給 review_tool 一個明確的人類可讀錯誤訊息。
    """
    projected = {k: v for k, v in annotation.items() if k in _GOLDEN_SCHEMA_FIELDS}
    missing = _GOLDEN_REQUIRED_FIELDS - set(projected)
    if missing:
        raise ValueError(
            f"促進至黃金題庫失敗：標注缺少必要欄位 {sorted(missing)}。"
            "請確認日誌含有 id、category、query 後再促進。"
        )
    return projected
