# ingest/src/anatomy_ingest/source.py
"""來源 producer：真實 PDF 與合成兩種，產出統一的 SourcePage（下游 encode/upload/write 相同）。

- pdf_source：DocumentConverter + pdf2image（需 docling 模型 + poppler；host gate）。
- synthetic_source：dev/CI 用，捏造 N 頁 PIL 影像 + 罐頭 Markdown（無 poppler/GPU/雲端）。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from PIL import Image, ImageDraw

from .classify import classify_page_type, extract_figures, map_anatomy_system
from .docling_parser import convert_pdf, extract_pages
from .page_render import render_pdf_pages
from .types import PageParse, SourcePage

_SYNTH_CHAPTERS = ("Upper Limb", "The Heart", "Cranial Nerves")
_SYNTH_BODY = "Synthetic page {n}. The structure is described here. See Fig. {n}-1."


def pdf_source(pdf_path: str, book_meta: dict[str, Any]) -> Iterator[SourcePage]:
    """真實路徑：PDF → SourcePage（解析 + 渲染對齊頁碼）。

    解析/渲染頁碼**對齊**（FIX C，Codex high #5）：
    - 每個被解析出的頁都產出一個 SourcePage——若渲染缺該頁影像，`image=None`（不靜默丟棄），
      由 cli 記 stage='render' 的 ingest_error 並計為失敗（§2.7）。
    - 渲染出但 Docling **未解析**的頁也不靜默丟棄：產出含 `parse_failed=True` placeholder 的
      SourcePage，由 cli 記 stage='parse' 的 ingest_error 並計為失敗。
    迭代 parses ∪ images 的 UNION 以確保兩種遺漏均被捕捉。
    """
    doc = convert_pdf(pdf_path)
    parses = {p.page_num: p for p in extract_pages(doc, book_meta)}
    images = render_pdf_pages(pdf_path)
    all_pages = sorted(set(parses) | set(images))
    for page_num in all_pages:
        if page_num in parses:
            yield SourcePage(parse=parses[page_num], image=images.get(page_num))  # 缺圖→None
        else:
            # 渲染出但 Docling 未解析：產出 parse_failed 佔位符，image 保留供 cli 記錯
            placeholder = PageParse(
                page_num=page_num,
                markdown="",
                metadata={"page_num": page_num, "parse_failed": True},
            )
            yield SourcePage(parse=placeholder, image=images.get(page_num))


def synthetic_source(n_pages: int, book_meta: dict[str, Any]) -> Iterator[SourcePage]:
    """合成路徑：決定性 N 頁。影像為帶頁碼文字的白底圖；Markdown 罐頭但走真實 classify。"""
    for n in range(1, n_pages + 1):
        chapter = _SYNTH_CHAPTERS[(n - 1) % len(_SYNTH_CHAPTERS)]
        markdown = f"## {chapter}\n\n" + _SYNTH_BODY.format(n=n)
        metadata = {
            "book_title": book_meta.get("book_title"),
            "edition": book_meta.get("edition"),
            "page_num": n,
            "chapter": chapter,
            "anatomy_system": map_anatomy_system(chapter, book_meta.get("system_map")),
            "page_type": classify_page_type(n_pictures=0, n_tables=0, text_len=len(markdown)),
            "figures": extract_figures(markdown),
        }
        img = Image.new("RGB", (800, 1000), "white")
        ImageDraw.Draw(img).text((20, 20), f"{chapter} p{n}", fill="black")
        yield SourcePage(
            parse=PageParse(page_num=n, markdown=markdown, metadata=metadata), image=img
        )
