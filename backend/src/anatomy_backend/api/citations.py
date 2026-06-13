"""引文衍生與真實性驗證（§5.7 / DL-012 / D-N）。

build_citations_and_images：RetrievalResult → PageCitation（前端）+ 依路由抓 LLM 影像 bytes。
[F5/H] fetch_bytes 為 async callable；routed 影像以 asyncio.gather 並行抓；sign_url 可留同步。
verify_citations：解析回答內行內引文 [書名, p.頁, Fig.圖]，cited (book, page) 對組對照 retrieved；
[F3/H] 書名正規化涵蓋 book_title 與 book_title+edition 兩種別名；figure 對照「該 book/page」的
figures[]；無法佐證者列 unverified（前端警告 banner、且不入快取）。強制引文是安全網核心。
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from anatomy_backend.api.schemas import PageCitation
from anatomy_backend.llm.image_routing import ImageRoutingDecision
from anatomy_backend.retrieval.types import RetrievalResult

logger = logging.getLogger(__name__)

_SNIPPET_LEN = 200
# 行內引文：[書名簡寫, p.頁碼 (或 頁碼), Fig.圖號（選填）]
_CITATION_RE = re.compile(
    r"\[\s*([^\],]+?)\s*,\s*p\.?\s*(\d+)\s*(?:,\s*([^\]]+?))?\s*\]",
    re.IGNORECASE,
)


def _norm_book(s: str) -> str:
    """去空白、小寫——使 'Gray 42' / 'Gray42' / 'gray42' 等正規化一致。"""
    return "".join(s.split()).lower()


async def build_citations_and_images(
    results: list[RetrievalResult],
    routing: ImageRoutingDecision,
    *,
    sign_url: Callable[[str], str],
    fetch_bytes: Callable[[str], Awaitable[bytes]],
) -> tuple[list[PageCitation], list[bytes]]:
    """RetrievalResult → PageCitation 串列 + routed 頁 bytes（並行抓、串流前完成）。

    sign_url 同步（本地 presign，無網路）；fetch_bytes 為 async（S3/MinIO）。
    routed 影像以 asyncio.gather 並行抓，加逾時保護（30s）。
    """
    citations: list[PageCitation] = []
    for r in results:
        figures = r.metadata.get("figures") or []
        citations.append(
            PageCitation(
                book_title=r.book_title,
                edition=r.edition,
                page=r.page_num,
                figure=(figures[0] if figures else None),
                image_url=sign_url(r.page_image_uri),
                snippet=r.docling_md[:_SNIPPET_LEN],
                score=r.score,
            )
        )
    if routing.indices:
        try:
            async with asyncio.timeout(30):
                imgs = list(
                    await asyncio.gather(
                        *(fetch_bytes(results[i].page_image_uri) for i in routing.indices)
                    )
                )
        except Exception:
            logger.warning("影像抓取失敗（逾時或網路錯誤），降級為無影像", exc_info=True)
            imgs = []
    else:
        imgs = []
    return citations, imgs


@dataclass(frozen=True)
class VerificationResult:
    has_citations: bool
    all_grounded: bool
    unverified: list[str]  # 未佐證引文的原文片段（供 log / 前端 banner）


def verify_citations(answer: str, results: list[RetrievalResult]) -> VerificationResult:
    """[F3/H] 以 (norm_book, page) 對組驗證引文；book 同時收 title 與 title+edition 兩種別名。

    figure 對照「該 book/page」的 figures[]（不混用跨頁）。
    """
    # 建 (norm_book, page) -> figures 集合；每個 result 同時注入兩種 book 別名
    pages_by_book: dict[tuple[str, int], set[str]] = {}
    for r in results:
        figs = {f.lower() for f in (r.metadata.get("figures") or [])}
        for alias in {
            _norm_book(r.book_title),
            _norm_book(r.book_title + (r.edition or "")),
        }:
            pages_by_book.setdefault((alias, r.page_num), set()).update(figs)

    matches = list(_CITATION_RE.finditer(answer))
    if not matches:
        return VerificationResult(has_citations=False, all_grounded=False, unverified=[])

    unverified: list[str] = []
    for m in matches:
        book = _norm_book(m.group(1))
        page = int(m.group(2))
        fig = (m.group(3) or "").strip().lower()
        figs = pages_by_book.get((book, page))
        ok = figs is not None and (not fig or fig in figs)
        if not ok:
            unverified.append(m.group(0))

    return VerificationResult(
        has_citations=True,
        all_grounded=not unverified,
        unverified=unverified,
    )
