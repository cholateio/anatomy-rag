"""Regression case 促進（§7.4，DL-028 [M-3]）。

promote_cases：把標注錯誤案例加入 regression JSONL 檔（去重 + 在記憶體內驗證 schema）。

關鍵設計（Codex [M-3]）：
- 驗證使用 parse_golden_row(c, 0) 在記憶體內執行——**不寫 tmp 檔**。
- 原子附加（open("a")）：只在所有驗證通過後才寫入。
- parent.mkdir(parents=True, exist_ok=True) 確保目錄存在。
"""
import json
from pathlib import Path

from anatomy_eval.golden import load_golden, parse_golden_row


def promote_cases(cases: list[dict], regression_path: str | Path) -> int:
    """把 cases 中通過 schema 驗證且尚未存在的項目附加到 regression JSONL 檔。

    Args:
        cases: 欲促進的案例 dict 清單（未經驗證的原始資料）。
        regression_path: regression JSONL 檔路徑（不存在則建立）。

    Returns:
        實際新增的案例數量。

    Raises:
        ValueError: 任一 case 未通過 parse_golden_row schema 驗證（非法即 raise，不寫入）。
    """
    p = Path(regression_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # 讀取已存在的 id 集合
    existing = {it.id for it in load_golden(p)} if p.exists() else set()

    to_add: list[dict] = []
    seen: set[str] = set()

    for c in cases:
        cid = c.get("id")
        if cid in existing or cid in seen:
            continue
        parse_golden_row(c, 0)  # 非法即 raise（記憶體內，無 tmp 檔）
        to_add.append(c)
        seen.add(cid)

    if to_add:
        with p.open("a", encoding="utf-8") as f:  # 原子附加
            for c in to_add:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    return len(to_add)
