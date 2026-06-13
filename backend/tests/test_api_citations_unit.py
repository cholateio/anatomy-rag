"""引文衍生與驗證單元測試（F3/F5 對抗式審查修正）。

F3: verify_citations 以 (book, page) 對組驗證（非頁碼單獨匹配）。
F5: build_citations_and_images 為 async；fetch_bytes 為 async callable。
"""
from uuid import uuid4

from anatomy_backend.api.citations import build_citations_and_images, verify_citations
from anatomy_backend.llm.image_routing import ImageRoutingDecision
from anatomy_backend.retrieval.types import RetrievalResult


def _r(page_num, book="Gray", edition="42", figs=("Fig.7-23",), pt="figure_heavy"):
    return RetrievalResult(
        page_id=uuid4(),
        score=0.9,
        book_title=book,
        edition=edition,
        page_num=page_num,
        page_image_uri=f"s3://b/p{page_num}.png",
        docling_md="肱二頭肌起於喙突。" * 30,
        metadata={"page_type": pt, "figures": list(figs)},
    )


async def _fetch(uri: str) -> bytes:
    return b"PNG:" + uri.encode()


async def test_build_citations_snippet_figure_and_signed_url():
    results = [_r(812), _r(813, figs=())]
    cits, imgs = await build_citations_and_images(
        results,
        ImageRoutingDecision(indices=(0,), detail="high"),
        sign_url=lambda uri: f"https://signed/{uri}",
        fetch_bytes=_fetch,
    )
    assert cits[0].page == 812 and cits[0].figure == "Fig.7-23"
    assert cits[0].image_url == "https://signed/s3://b/p812.png"
    assert len(cits[0].snippet) <= 200
    assert cits[1].figure is None  # 無 figures → None
    assert imgs == [b"PNG:s3://b/p812.png"]  # 只抓 routed index 0


async def test_build_citations_no_images_when_routing_empty():
    results = [_r(812)]
    cits, imgs = await build_citations_and_images(
        results,
        ImageRoutingDecision(indices=()),
        sign_url=lambda u: u,
        fetch_bytes=_fetch,
    )
    assert imgs == [] and len(cits) == 1


async def test_build_citations_fetches_routed_indices_concurrently():
    """F5: routed indices 以 asyncio.gather 並行抓；多 routed 頁各自取到正確 bytes。"""
    results = [_r(812), _r(813), _r(814)]
    fetch_calls: list[str] = []

    async def _track_fetch(uri: str) -> bytes:
        fetch_calls.append(uri)
        return b"PNG:" + uri.encode()

    cits, imgs = await build_citations_and_images(
        results,
        ImageRoutingDecision(indices=(0, 2), detail="high"),
        sign_url=lambda u: u,
        fetch_bytes=_track_fetch,
    )
    # 只抓 indices 0 和 2，不抓 index 1
    assert len(imgs) == 2
    assert b"PNG:s3://b/p812.png" in imgs
    assert b"PNG:s3://b/p814.png" in imgs
    assert set(fetch_calls) == {"s3://b/p812.png", "s3://b/p814.png"}


def test_verify_citations_grounded_and_unverified():
    results = [_r(812, book="Gray", figs=("Fig.7-23",)), _r(813, book="Gray", figs=())]
    # p.999 不在 retrieved
    answer = "肱二頭肌起於喙突 [Gray, p.812, Fig.7-23]。某段落 [Gray, p.999]。"
    v = verify_citations(answer, results)
    assert v.has_citations is True
    assert v.all_grounded is False
    assert any("p.999" in u or "999" in u for u in v.unverified)


def test_verify_citations_figure_not_on_page_is_unverified():
    results = [_r(812, figs=("Fig.7-23",))]
    answer = "x [Gray, p.812, Fig.9-99]。"  # 頁對、圖號不在該頁 figures[]
    v = verify_citations(answer, results)
    assert v.all_grounded is False


def test_verify_citations_all_grounded():
    results = [_r(812, figs=("Fig.7-23",))]
    answer = "肱二頭肌起於喙突 [Gray, p.812, Fig.7-23]。"
    v = verify_citations(answer, results)
    assert v.all_grounded is True and v.unverified == []


def test_verify_citations_no_citation_flagged():
    v = verify_citations("一段沒有任何引文的文字。", [_r(812)])
    assert v.has_citations is False and v.all_grounded is False


# F3 cross-book tests: (book, page) 對組驗證，跨書同頁碼必須分清楚
def test_verify_citations_cross_book_wrong_book_is_unverified():
    """[FakeBook, p.812] 在 retrieved 只有 Gray p.812 時 → unverified（跨書同頁不通）。"""
    results = [_r(812, book="Gray", edition="42", figs=("Fig.7-23",))]
    answer = "某段落 [FakeBook, p.812]。"
    v = verify_citations(answer, results)
    assert v.has_citations is True
    assert v.all_grounded is False
    assert any("FakeBook" in u or "812" in u for u in v.unverified)


def test_verify_citations_cross_book_title_plus_edition_alias_verified():
    """[Gray42, p.812] abbrev = title+edition → verified（_norm_book 別名機制）。"""
    results = [_r(812, book="Gray", edition="42", figs=("Fig.7-23",))]
    # "Gray42" 正規化後 = "gray42"，與 _norm_book("Gray" + "42") = "gray42" 相符
    answer = "某段落 [Gray42, p.812, Fig.7-23]。"
    v = verify_citations(answer, results)
    assert v.has_citations is True
    assert v.all_grounded is True
    assert v.unverified == []
