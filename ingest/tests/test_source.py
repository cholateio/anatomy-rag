# ingest/tests/test_source.py
from anatomy_ingest.source import synthetic_source
from anatomy_ingest.types import SourcePage
from PIL import Image


def test_synthetic_source_yields_n_pages():
    meta = {"book_title": "Synthetic Atlas", "edition": 1}
    pages = list(synthetic_source(n_pages=3, book_meta=meta))
    assert len(pages) == 3
    assert all(isinstance(p, SourcePage) for p in pages)
    nums = [p.parse.page_num for p in pages]
    assert nums == [1, 2, 3]


def test_synthetic_source_metadata_and_image():
    meta = {"book_title": "Synthetic Atlas", "edition": 1}
    p = next(iter(synthetic_source(n_pages=1, book_meta=meta)))
    assert p.parse.metadata["book_title"] == "Synthetic Atlas"
    assert p.parse.metadata["page_num"] == 1
    assert p.parse.metadata["page_type"] in ("pure_text", "figure_heavy", "table", "mixed")
    assert isinstance(p.image, Image.Image)


def test_synthetic_source_deterministic_markdown():
    meta = {"book_title": "A", "edition": 1}
    a = [sp.parse.markdown for sp in synthetic_source(2, meta)]
    b = [sp.parse.markdown for sp in synthetic_source(2, meta)]
    assert a == b


def test_pdf_source_yields_none_image_for_missing_render(monkeypatch):
    """渲染缺頁時 pdf_source 仍產出該頁（image=None），不靜默丟棄（Codex high #1）。"""
    import anatomy_ingest.source as src
    from anatomy_ingest.types import PageParse
    from PIL import Image

    parses = [
        PageParse(page_num=1, markdown="p1", metadata={"page_num": 1}),
        PageParse(page_num=2, markdown="p2", metadata={"page_num": 2}),
        PageParse(page_num=3, markdown="p3", metadata={"page_num": 3}),
    ]
    monkeypatch.setattr(src, "convert_pdf", lambda path: object())
    monkeypatch.setattr(src, "extract_pages", lambda doc, meta: parses)
    # 只渲染出 1、3 頁（缺第 2 頁）
    monkeypatch.setattr(src, "render_pdf_pages",
                        lambda path: {1: Image.new("RGB", (4, 4)), 3: Image.new("RGB", (4, 4))})
    out = list(src.pdf_source("x.pdf", {"book_title": "A"}))
    assert [sp.parse.page_num for sp in out] == [1, 2, 3]  # 三頁都產出，無遺漏
    assert out[1].image is None and out[0].image is not None and out[2].image is not None


def test_pdf_source_yields_parse_failed_for_docling_omitted_page(monkeypatch):
    """Docling 未解析但已渲染的頁：pdf_source 產出 parse_failed=True 佔位符，不靜默丟棄（FIX C）。

    parses={1,3}，images={1,2,3}：第 2 頁只有影像沒有解析 → 應產出 3 頁，
    page 2 的 parse.metadata["parse_failed"] is True，且 image 保留。
    pages 1/3 為正常頁（無 parse_failed）。
    """
    import anatomy_ingest.source as src
    from anatomy_ingest.types import PageParse
    from PIL import Image

    parses = [
        PageParse(page_num=1, markdown="p1", metadata={"page_num": 1}),
        PageParse(page_num=3, markdown="p3", metadata={"page_num": 3}),
    ]
    img1 = Image.new("RGB", (4, 4))
    img2 = Image.new("RGB", (4, 4))
    img3 = Image.new("RGB", (4, 4))
    monkeypatch.setattr(src, "convert_pdf", lambda path: object())
    monkeypatch.setattr(src, "extract_pages", lambda doc, meta: parses)
    monkeypatch.setattr(src, "render_pdf_pages", lambda path: {1: img1, 2: img2, 3: img3})

    out = list(src.pdf_source("x.pdf", {"book_title": "A"}))

    assert [sp.parse.page_num for sp in out] == [1, 2, 3], "三頁均須產出"
    # page 2：parse_failed 佔位符，image 保留
    assert out[1].parse.metadata.get("parse_failed") is True, "page 2 須有 parse_failed=True"
    assert out[1].image is img2, "page 2 影像須保留"
    assert out[1].parse.markdown == "", "page 2 markdown 應為空字串"
    # pages 1/3：正常頁，無 parse_failed
    assert not out[0].parse.metadata.get("parse_failed"), "page 1 不應有 parse_failed"
    assert not out[2].parse.metadata.get("parse_failed"), "page 3 不應有 parse_failed"
    assert out[0].image is img1
    assert out[2].image is img3
