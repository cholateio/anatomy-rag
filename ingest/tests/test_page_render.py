# ingest/tests/test_page_render.py
from anatomy_ingest.page_render import MAX_LONG_EDGE, resize_long_edge
from PIL import Image


def test_resize_downscales_long_edge():
    img = Image.new("RGB", (4000, 2000), "white")
    out = resize_long_edge(img)
    assert max(out.size) == MAX_LONG_EDGE
    assert out.size == (MAX_LONG_EDGE, MAX_LONG_EDGE // 2)  # 維持長寬比


def test_resize_noop_when_small():
    img = Image.new("RGB", (1000, 800), "white")
    out = resize_long_edge(img)
    assert out.size == (1000, 800)


def test_resize_portrait():
    img = Image.new("RGB", (1500, 3000), "white")
    out = resize_long_edge(img)
    assert max(out.size) == MAX_LONG_EDGE and out.size == (MAX_LONG_EDGE // 2, MAX_LONG_EDGE)
