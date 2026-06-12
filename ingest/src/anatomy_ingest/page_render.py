"""頁面渲染：render_pdf_pages（thin，pdf2image，需 poppler）+ resize_long_edge（純）。

§2.1：PNG 200 DPI、長邊上限 2048 px。render 在 CI 不跑（無 poppler）；resize 純邏輯可測。
"""
from __future__ import annotations

from PIL import Image

RENDER_DPI = 200
MAX_LONG_EDGE = 2048


def resize_long_edge(img: Image.Image, max_long_edge: int = MAX_LONG_EDGE) -> Image.Image:
    """等比縮放使長邊 ≤ max_long_edge；已在範圍內則原樣回傳。"""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge:
        return img
    scale = max_long_edge / long_edge
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def render_pdf_pages(pdf_path: str, dpi: int = RENDER_DPI) -> dict[int, Image.Image]:
    """真實路徑：PDF → {page_num(1-indexed): PIL.Image}（已 resize 長邊）。需 poppler。"""
    from pdf2image import convert_from_path  # lazy：需 poppler runtime

    images = convert_from_path(pdf_path, dpi=dpi)
    return {i: resize_long_edge(img.convert("RGB")) for i, img in enumerate(images, start=1)}
