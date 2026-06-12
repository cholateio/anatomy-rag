# ingest/src/anatomy_ingest/docling_parser.py
"""Docling 解析：convert_pdf（thin，真實 PDF→DoclingDocument）+ extract_pages（純抽取）。

convert_pdf 包裝 DocumentConverter（重、需 docling 模型，不進 CI）；extract_pages 對
已建好的 DoclingDocument 逐頁抽 Markdown + 規範化 metadata（純邏輯，單元測試直測）。
MUST NOT 呼叫任何雲端 LLM（離線管線紅線）。
"""
from __future__ import annotations

from typing import Any

from docling_core.types.doc import DocItemLabel

from .classify import classify_page_type, extract_figures, map_anatomy_system
from .types import PageParse


def convert_pdf(pdf_path: str):
    """真實路徑：PDF → DoclingDocument。需 docling 模型（首次下載），不在 CI 執行。"""
    from docling.document_converter import DocumentConverter  # lazy：重依賴

    return DocumentConverter().convert(pdf_path).document


def _page_numbers(doc) -> list[int]:
    pages = getattr(doc, "pages", None)
    if pages:
        return sorted(int(n) for n in pages.keys())
    # 後備：從 items 的 prov 收集
    nums = {prov.page_no for item, _ in doc.iterate_items() for prov in getattr(item, "prov", [])}
    return sorted(nums)


def _page_stats(doc, page_no: int) -> tuple[str | None, int, int]:
    """回 (該頁第一個 section_header 文字, 圖片數, 表格數)。"""
    chapter = None
    n_pictures = n_tables = 0
    for item, _level in doc.iterate_items():
        provs = getattr(item, "prov", [])
        if not any(p.page_no == page_no for p in provs):
            continue
        label = getattr(item, "label", None)
        if label == DocItemLabel.SECTION_HEADER and chapter is None:
            chapter = getattr(item, "text", None)
        elif label == DocItemLabel.PICTURE:
            n_pictures += 1
        elif label == DocItemLabel.TABLE:
            n_tables += 1
    return chapter, n_pictures, n_tables


def extract_pages(doc, book_meta: dict[str, Any]) -> list[PageParse]:
    """DoclingDocument → 每頁 PageParse（Markdown + 規範化 metadata，§3.2）。

    chapter 沿用：某頁無新 section_header 時，沿用前一頁章節（教科書段落跨頁常態）。
    overrides：book_meta['system_map']（小寫章節全名 → anatomy_system）。
    """
    overrides = book_meta.get("system_map")
    out: list[PageParse] = []
    last_chapter: str | None = None
    for page_no in _page_numbers(doc):
        markdown = doc.export_to_markdown(page_no=page_no)
        chapter, n_pictures, n_tables = _page_stats(doc, page_no)
        if chapter:
            last_chapter = chapter
        else:
            chapter = last_chapter
        figures = extract_figures(markdown)
        metadata = {
            "book_title": book_meta.get("book_title"),
            "edition": book_meta.get("edition"),
            "page_num": page_no,
            "chapter": chapter,
            "anatomy_system": map_anatomy_system(chapter, overrides),
            "page_type": classify_page_type(n_pictures, n_tables, len(markdown)),
            "figures": figures,
        }
        out.append(PageParse(page_num=page_no, markdown=markdown, metadata=metadata))
    return out
