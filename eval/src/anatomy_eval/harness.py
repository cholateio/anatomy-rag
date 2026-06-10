"""最小 recall@K by question-class harness（D-P / DL-013 上線 gate 的種子）。

retrieve_fn 為引擎中立介面：給 GoldenQA、回傳排序後 page_id 字串列表。
Phase 3/5 將以真實 encoder/檢索接上同一介面；out_of_scope 題不計 retrieval recall
（其正確性屬生成層 gate，§7.2/§7.3）。本 harness 的合成測試僅為管線煙霧驗證，
不構成 DL-013 四變體實測 gate 的通過宣稱。
"""
from collections.abc import Callable, Sequence

from anatomy_eval.golden import GoldenQA


def recall_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    """expected 中出現在 retrieved 前 k 名的比例。"""
    if not expected:
        raise ValueError("expected 不可為空（out_of_scope 題不計 recall）")
    top = set(list(retrieved)[:k])
    return sum(1 for p in expected if p in top) / len(expected)


def evaluate_recall_by_class(
    golden: Sequence[GoldenQA],
    retrieve_fn: Callable[[GoldenQA], Sequence[str]],
    k: int = 10,
) -> dict:
    """對黃金題庫逐題呼叫 retrieve_fn，回報 recall@K（依 category 分組 + overall）。"""
    per_class: dict[str, list[float]] = {}
    skipped = 0
    for qa in golden:
        if qa.category == "out_of_scope":
            skipped += 1
            continue
        score = recall_at_k(retrieve_fn(qa), qa.expected_pages, k)
        per_class.setdefault(qa.category, []).append(score)
    all_scores = [s for scores in per_class.values() for s in scores]
    return {
        "k": k,
        "n_evaluated": len(all_scores),
        "n_skipped_oos": skipped,
        "by_class": {c: sum(v) / len(v) for c, v in per_class.items()},
        "overall": (sum(all_scores) / len(all_scores)) if all_scores else 0.0,
    }
