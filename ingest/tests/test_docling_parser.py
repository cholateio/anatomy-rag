# ingest/tests/test_docling_parser.py
from anatomy_ingest.docling_parser import extract_pages
from anatomy_ingest.types import PageParse
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)


def _two_page_doc():
    doc = DoclingDocument(name="t")
    doc.add_page(page_no=1, size=Size(width=612, height=792))
    doc.add_page(page_no=2, size=Size(width=612, height=792))
    p1 = ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=100, b=10), charspan=(0, 10))
    doc.add_heading(text="Upper Limb", prov=p1)
    doc.add_text(
        label=DocItemLabel.TEXT,
        text="The brachial plexus. See Fig. 7-23.",
        prov=ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=20, r=100, b=30), charspan=(0, 10)),
    )
    doc.add_text(
        label=DocItemLabel.TEXT,
        text="Continued text on page two.",
        prov=ProvenanceItem(page_no=2, bbox=BoundingBox(l=0, t=0, r=100, b=10), charspan=(0, 10)),
    )
    return doc


def test_extract_pages_count_and_markdown():
    pages = extract_pages(_two_page_doc(), book_meta={"book_title": "Gray", "edition": 42})
    assert len(pages) == 2
    assert all(isinstance(p, PageParse) for p in pages)
    assert pages[0].page_num == 1 and "Upper Limb" in pages[0].markdown
    assert pages[1].page_num == 2 and "page two" in pages[1].markdown


def test_extract_pages_metadata_normalized():
    pages = extract_pages(_two_page_doc(), book_meta={"book_title": "Gray", "edition": 42})
    m = pages[0].metadata
    assert m["book_title"] == "Gray" and m["edition"] == 42 and m["page_num"] == 1
    assert m["chapter"] == "Upper Limb"
    assert m["anatomy_system"] == "musculoskeletal"
    assert m["page_type"] in ("pure_text", "figure_heavy", "table", "mixed")
    assert m["figures"] == ["Fig. 7-23"]


def test_extract_pages_chapter_carries_forward():
    # 第二頁無新標題 → 沿用第一頁章節
    pages = extract_pages(_two_page_doc(), book_meta={"book_title": "Gray", "edition": 42})
    assert pages[1].metadata["chapter"] == "Upper Limb"
    assert pages[1].metadata["anatomy_system"] == "musculoskeletal"
