"""頁面分類純函式：chapter→anatomy_system 對照、page_type 啟發、figures 抽取。

全為純函式（無 IO），供 docling_parser 組 metadata 用，便於單元測試。
枚舉值對齊 ARCHITECTURE.md §3.2 metadata 規範。
"""
from __future__ import annotations

import re

ANATOMY_SYSTEMS = (
    "musculoskeletal", "cardiovascular", "nervous", "respiratory", "digestive",
    "urogenital", "endocrine", "integumentary", "lymphatic", "special_senses", "other",
)
PAGE_TYPES = ("pure_text", "figure_heavy", "table", "mixed")

# 章節關鍵字 → anatomy_system（小寫子字串比對；長詞優先）。book-meta 可用 overrides 補充/覆寫。
_SYSTEM_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("upper limb", "musculoskeletal"), ("lower limb", "musculoskeletal"),
    ("muscle", "musculoskeletal"), ("bone", "musculoskeletal"), ("joint", "musculoskeletal"),
    ("skeleton", "musculoskeletal"), ("back", "musculoskeletal"),
    ("heart", "cardiovascular"), ("vessel", "cardiovascular"), ("artery", "cardiovascular"),
    ("vein", "cardiovascular"), ("cardiovascular", "cardiovascular"), ("vascular", "cardiovascular"),
    ("nerve", "nervous"), ("brain", "nervous"), ("spinal cord", "nervous"),
    ("nervous", "nervous"), ("cranial", "nervous"), ("neuro", "nervous"),
    ("lung", "respiratory"), ("respiratory", "respiratory"), ("trachea", "respiratory"),
    ("bronch", "respiratory"), ("pleura", "respiratory"),
    ("stomach", "digestive"), ("intestine", "digestive"), ("liver", "digestive"),
    ("digestive", "digestive"), ("gastro", "digestive"), ("abdomen", "digestive"),
    ("kidney", "urogenital"), ("bladder", "urogenital"), ("urogenital", "urogenital"),
    ("renal", "urogenital"), ("reproductive", "urogenital"), ("pelvis", "urogenital"),
    ("thyroid", "endocrine"), ("pituitary", "endocrine"), ("endocrine", "endocrine"),
    ("adrenal", "endocrine"),
    ("skin", "integumentary"), ("integument", "integumentary"),
    ("lymph", "lymphatic"), ("spleen", "lymphatic"), ("immune", "lymphatic"),
    ("eye", "special_senses"), ("ear", "special_senses"), ("special sense", "special_senses"),
    ("orbit", "special_senses"),
)

# 長關鍵字優先比對（"spinal cord" 早於 "cord"），降低短詞誤命中
_SYSTEM_KEYWORDS_SORTED = tuple(sorted(_SYSTEM_KEYWORDS, key=lambda kv: -len(kv[0])))

_FIGURE_RE = re.compile(r"\b(fig(?:ure)?\.?\s*\d+[-.]?\d*)", re.IGNORECASE)


def map_anatomy_system(chapter: str | None, overrides: dict[str, str] | None = None) -> str:
    """章節名稱 → anatomy_system 枚舉值；無命中回 'other'。overrides 鍵為小寫章節全名。"""
    if not chapter:
        return "other"
    key = chapter.strip().lower()
    if overrides and key in overrides and overrides[key] in ANATOMY_SYSTEMS:
        return overrides[key]
    for kw, system in _SYSTEM_KEYWORDS_SORTED:
        if kw in key:
            return system
    return "other"


def classify_page_type(n_pictures: int, n_tables: int, text_len: int) -> str:
    """啟發式頁型分類（§3.2 枚舉）。閾值刻意保守，Phase 11 真實教材再校。"""
    if n_tables >= 2 and text_len < 400:
        return "table"
    if n_pictures >= 2 and text_len < 300:
        return "figure_heavy"
    if n_pictures == 0 and n_tables == 0:
        return "pure_text"
    return "mixed"


def extract_figures(markdown: str) -> list[str]:
    """從 Markdown 抽圖說標籤（'Fig. 7-23'/'Figure 8.4'/'fig 9-1'）；正規化大小寫、去重保序。"""
    seen: dict[str, None] = {}
    for m in _FIGURE_RE.finditer(markdown):
        raw = re.sub(r"\s+", " ", m.group(1).strip())
        # 正規化前綴：fig→Fig.、figure→Figure
        body = re.sub(r"^fig(ure)?\.?\s*", "", raw, flags=re.IGNORECASE)
        prefix = "Figure" if re.match(r"^fig(ure)\b", raw, re.IGNORECASE) else "Fig."
        norm = f"{prefix} {body}"
        seen.setdefault(norm, None)
    return list(seen.keys())
