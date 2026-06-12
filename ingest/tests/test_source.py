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
