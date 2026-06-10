"""最小 recall@K by question-class harness（D-P / DL-013 上線 gate 的種子）。

retrieve_fn 為引擎中立介面：給 GoldenQA、回傳排序後 page_id 字串列表。
Phase 3/5 將以真實 encoder/檢索接上同一介面；out_of_scope 題不計 retrieval recall
（其正確性屬生成層 gate，§7.2/§7.3）。本 harness 的合成測試僅為管線煙霧驗證，
不構成 DL-013 四變體實測 gate 的通過宣稱。
"""
from collections.abc import Callable, Sequence

from anatomy_eval.golden import GoldenQA


def recall_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    """expected 中出現在 retrieved 前 k 名的比例。

    retrieved 與 expected 均先保序去重，避免重複項扭曲 k 窗或分母。
    """
    if k < 1:
        raise ValueError(f"k 必須 >= 1，收到 k={k}")
    if not expected:
        raise ValueError("expected 不可為空（out_of_scope 題不計 recall）")
    # 保序去重 retrieved，再取前 k
    seen: set[str] = set()
    unique_retrieved = [p for p in retrieved if not (p in seen or seen.add(p))]
    top = set(unique_retrieved[:k])
    # 保序去重 expected，避免重複項灌水分母
    uniq_expected = list(dict.fromkeys(expected))
    return sum(1 for p in uniq_expected if p in top) / len(uniq_expected)


def evaluate_recall_by_class(
    golden: Sequence[GoldenQA],
    retrieve_fn: Callable[[GoldenQA], Sequence[str]],
    k: int = 10,
) -> dict:
    """對黃金題庫逐題呼叫 retrieve_fn，回報 recall@K（依 category 分組 + overall）。

    overall 為 micro-average：所有可評估題目逐題 recall 的算術平均，
    並非各類別平均值的再平均（macro-average）。

    回傳 dict 包含：
    - k: 使用的 k 值
    - n_evaluated: 實際評估的題目數（不含 out_of_scope）
    - n_skipped_oos: 跳過的 out_of_scope 題數
    - by_class: 各類別的平均 recall
    - n_by_class: 各類別的題目數（gate 工具用於判斷樣本數充足性）
    - overall: micro-average recall（逐題平均）

    Raises:
        ValueError: 若 n_evaluated == 0（空題庫或全為 out_of_scope），
            gate 不得在無評估證據下通過。
    """
    per_class: dict[str, list[float]] = {}
    skipped = 0
    for qa in golden:
        if qa.category == "out_of_scope":
            skipped += 1
            continue
        score = recall_at_k(retrieve_fn(qa), qa.expected_pages, k)
        per_class.setdefault(qa.category, []).append(score)
    all_scores = [s for scores in per_class.values() for s in scores]
    n_evaluated = len(all_scores)
    if n_evaluated == 0:
        raise ValueError(
            "沒有可評估的題目（空題庫或全為 out_of_scope）——gate 不得在無證據下通過"
        )
    return {
        "k": k,
        "n_evaluated": n_evaluated,
        "n_skipped_oos": skipped,
        "by_class": {c: sum(v) / len(v) for c, v in per_class.items()},
        "n_by_class": {c: len(v) for c, v in per_class.items()},
        "overall": sum(all_scores) / n_evaluated,
    }
