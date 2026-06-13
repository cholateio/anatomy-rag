import uuid

from anatomy_backend.retrieval.types import RetrievalResult


def test_retrieval_result_fields():
    pid = uuid.uuid4()
    r = RetrievalResult(
        page_id=pid, score=0.5, book_title="Gray's", edition="42e",
        page_num=12, page_image_uri="s3://x.png", docling_md="# md",
        metadata={"figures": ["12.3"]},
    )
    assert r.page_id == pid
    assert r.score == 0.5
    assert r.metadata["figures"] == ["12.3"]
    assert r.edition == "42e"


def test_engine_result_degraded_flag():
    from anatomy_backend.retrieval.types import EngineResult
    pid = uuid.uuid4()
    er = EngineResult(ranked=[(pid, 3.0)], coarse_ids=[pid], degraded=False)
    assert er.ranked[0] == (pid, 3.0)
    assert er.coarse_ids == [pid]
    assert er.degraded is False
