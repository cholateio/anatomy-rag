from uuid import uuid4

from anatomy_backend.llm.image_routing import (
    DEFAULT_IMAGE_COUNT,
    DL009_MAX_IMAGES,
    QueryIntent,
    route_images,
)
from anatomy_backend.retrieval.types import RetrievalResult


def _r(page_type: str, page_num: int = 1) -> RetrievalResult:
    return RetrievalResult(
        page_id=uuid4(),
        score=1.0,
        book_title="Gray",
        edition="42",
        page_num=page_num,
        page_image_uri="s3://x",
        docling_md="…",
        metadata={"page_type": page_type, "figures": ["Fig.7-23"]},
    )


def test_pure_text_intent_sends_zero_images():
    results = [_r("figure_heavy"), _r("mixed")]
    decision = route_images(results, QueryIntent.PURE_TEXT)
    assert decision.indices == ()


def test_figure_intent_default_is_top_one_high_detail():
    # DL-009：預設 top-1（即使有多張 figure_heavy）
    results = [_r("figure_heavy"), _r("mixed"), _r("figure_heavy")]
    decision = route_images(results, QueryIntent.FIGURE)
    assert decision.indices == (0,)
    assert decision.detail == "high"
    assert DEFAULT_IMAGE_COUNT == 1


def test_figure_intent_explicit_two_capped_at_max():
    results = [_r("figure_heavy"), _r("mixed"), _r("figure_heavy")]
    decision = route_images(results, QueryIntent.FIGURE, max_images=2)
    assert decision.indices == (0, 1)  # 依 RRF 既有順序


def test_max_images_clamped_to_hard_cap():
    results = [_r("figure_heavy"), _r("mixed"), _r("figure_heavy"), _r("mixed")]
    decision = route_images(results, QueryIntent.FIGURE, max_images=99)
    assert len(decision.indices) == DL009_MAX_IMAGES == 2


def test_figure_intent_skips_pure_text_and_table_pages():
    results = [_r("pure_text"), _r("table"), _r("figure_heavy")]
    decision = route_images(results, QueryIntent.FIGURE, max_images=2)
    assert decision.indices == (2,)


def test_figure_intent_no_eligible_pages_sends_zero():
    results = [_r("pure_text"), _r("table")]
    decision = route_images(results, QueryIntent.FIGURE)
    assert decision.indices == ()


def test_missing_page_type_metadata_treated_as_non_figure():
    r = RetrievalResult(uuid4(), 1.0, "Gray", "42", 1, "s3://x", "…", metadata={})
    decision = route_images([r], QueryIntent.FIGURE)
    assert decision.indices == ()
