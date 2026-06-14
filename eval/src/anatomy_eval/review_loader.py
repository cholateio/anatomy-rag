"""Streamlit 抽檢工具的資料層（§7.4）。

此模組為純 Python，不依賴 Streamlit，可獨立單元測試。
業務邏輯全在此；review_tool.py 為薄 Streamlit shell。

函式：
    sample_logs(rows, n, *, seed) — 決定性隨機抽樣，clinical_flavored 優先排前。
    export_annotations(annotations, path) — 匯出標注至 JSONL；label 非法即 raise。
"""
import json
import random
from pathlib import Path

# §7.4 合法標注標籤
VALID_LABELS: frozenset[str] = frozenset({"correct", "partial", "wrong"})


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
