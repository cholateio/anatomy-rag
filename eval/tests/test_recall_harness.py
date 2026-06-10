"""recall@K harness 測試：單元 + 合成資料煙霧（D-P gate 種子）。"""
import numpy as np
import pytest
from anatomy_eval.golden import GoldenQA
from anatomy_eval.harness import evaluate_recall_by_class, recall_at_k
from anatomy_eval.reference import maxsim_hamming
from anatomy_shared.binary import binarize
from anatomy_shared.colpali_runtime import MockColPaliRuntime


def test_recall_at_k_basic():
    assert recall_at_k(["a", "b", "c"], ["a"], k=1) == 1.0
    assert recall_at_k(["a", "b", "c"], ["c"], k=2) == 0.0
    assert recall_at_k(["a", "b", "c"], ["a", "z"], k=3) == 0.5


def test_recall_at_k_rejects_empty_expected():
    with pytest.raises(ValueError):
        recall_at_k(["a"], [], k=3)


def _mk(qid, cat, query, pages):
    return GoldenQA(id=qid, category=cat, query=query, expected_pages=tuple(pages))


def test_evaluate_skips_oos_and_groups_by_class():
    golden = [
        _mk("t1", "text_only", "q1", ["p1"]),
        _mk("f1", "figure_id", "q2", ["p9"]),
        GoldenQA(id="o1", category="out_of_scope", query="oos",
                 expected_response_type="教材中查無此項"),
    ]
    # 固定回傳 p1, p2, ...：t1 命中、f1 未命中
    report = evaluate_recall_by_class(golden, lambda qa: ["p1", "p2", "p3"], k=3)
    assert report["k"] == 3
    assert report["n_evaluated"] == 2 and report["n_skipped_oos"] == 1
    assert report["by_class"]["text_only"] == 1.0
    assert report["by_class"]["figure_id"] == 0.0
    assert report["overall"] == 0.5


def test_synthetic_smoke_binary_maxsim_recall_pipeline():
    """合成語料煙霧測試：mock runtime 產頁面 patch → binarize → binary MaxSim 檢索 →
    recall@3 by class 應為 1.0（query 取自目標頁的 patch 子集 + 雜訊，自我檢索必中）。
    僅驗證 harness 管線可運轉；**非** DL-013 gate（四變體實測在 Phase 3/5 對真實管線執行）。
    """
    rt = MockColPaliRuntime()
    rng = np.random.default_rng(42)
    page_ids = [f"fake:{i}" for i in range(20)]
    corpus = {pid: [binarize(v) for v in rt.encode_page(pid).embeddings] for pid in page_ids}

    def query_tokens_for(pid: str) -> list[bytes]:
        vecs = rt.encode_page(pid).embeddings[:8]       # 取該頁 8 個 patch
        noisy = vecs + rng.normal(0, 0.05, vecs.shape)  # 小雜訊（不翻轉多數符號）
        return [binarize(v.astype("float32")) for v in noisy]

    def retrieve(qa: GoldenQA) -> list[str]:
        tokens = query_tokens_for(qa.expected_pages[0])
        scored = sorted(page_ids, key=lambda pid: -maxsim_hamming(tokens, corpus[pid]))
        return scored

    golden = [
        _mk("s1", "text_only", "q", ["fake:0"]),
        _mk("s2", "text_only", "q", ["fake:1"]),
        _mk("s3", "figure_id", "q", ["fake:2"]),
        _mk("s4", "cross_page", "q", ["fake:3"]),
        _mk("s5", "clinical_correlation", "q", ["fake:4"]),
    ]
    report = evaluate_recall_by_class(golden, retrieve, k=3)
    assert report["overall"] == 1.0
    expected_classes = {"text_only", "figure_id", "cross_page", "clinical_correlation"}
    assert set(report["by_class"]) == expected_classes
    assert all(v == 1.0 for v in report["by_class"].values())
