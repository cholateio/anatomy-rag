"""檢索評分的純 Python 參考實作——Phase 5 SQL/應用層實作的測試 oracle。"""
from collections.abc import Sequence

from anatomy_shared.binary import hamming_distance


def maxsim_hamming(query_tokens_bin: Sequence[bytes], page_patches_bin: Sequence[bytes]) -> float:
    """§4.4 MaxSim：score(page) = Σ_t max_p (128 - hamming(t, p))。

    小規模 O(T×P) 直算，僅供測試/評估對照；線上路徑由 Stage B（SQL 或 numpy）負責。
    128-distance 的相似度轉換只對 bit(128) 成立，故強制 16-byte 輸入。
    """
    if not query_tokens_bin or not page_patches_bin:
        raise ValueError("query tokens 與 page patches 皆不可為空")
    for v in (*query_tokens_bin, *page_patches_bin):
        if len(v) != 16:
            raise ValueError("maxsim_hamming 僅支援 bit(128)＝16-byte 輸入")
    return float(sum(
        max(128 - hamming_distance(t, p) for p in page_patches_bin)
        for t in query_tokens_bin
    ))
