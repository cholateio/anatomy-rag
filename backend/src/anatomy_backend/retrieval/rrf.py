"""Reciprocal Rank Fusion（§4.5）。"""
from uuid import UUID


def rrf_fuse(rank_lists: list[list[UUID]], k: int = 60) -> list[tuple[UUID, float]]:
    """rank_lists：每個 list 按相關性遞減；回傳融合後 (page_id, score) 按 score 遞減。"""
    scores: dict[UUID, float] = {}
    for ranks in rank_lists:
        for rank, pid in enumerate(ranks):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))
