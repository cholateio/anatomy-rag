import uuid

from anatomy_backend.retrieval.rrf import rrf_fuse


def test_rrf_single_list_preserves_order():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = rrf_fuse([[a, b, c]])
    assert [pid for pid, _ in fused] == [a, b, c]


def test_rrf_rewards_consensus():
    a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # a 在兩表都高名次 → 應勝過任一單表的 rank0
    fused = rrf_fuse([[a, b, c], [a, d]])
    assert fused[0][0] == a
    # 分數 = 1/(60+0) + 1/(60+0) for a
    assert abs(fused[0][1] - (1.0 / 60 + 1.0 / 60)) < 1e-9


def test_rrf_formula_and_k():
    a = uuid.uuid4()
    fused = rrf_fuse([[uuid.uuid4(), a]], k=10)  # a 在 rank 1
    assert abs(fused[1][1] - 1.0 / (10 + 1)) < 1e-9  # plan had fused[0][1] — bug: a is rank 1 so score lands at index 1


def test_rrf_empty_lists():
    assert rrf_fuse([[], []]) == []
