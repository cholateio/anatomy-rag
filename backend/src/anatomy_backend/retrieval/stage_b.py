"""Stage B — MaxSim 精排（§4.4）。

score(page) = Σ_t max_p (128 - hamming(token_t, patch_p))

兩條等價路徑（§4.4）：
- stage_b_maxsim：SQL 聚合（pgvector `<~>` Hamming），spec 主路徑。
- stage_b_maxsim_numpy：撈候選頁 patch_bin 後應用層 numpy XOR+popcount（並發退路）。
預設由 Stage B 並發/p95 benchmark gate 決定（見 SelfBuiltEngine.stage_b_mode）。

query tokens 經 shared.binary.to_pg_bits（唯一位序轉換點）轉 128-char '0'/'1' → ::bit(128)。
MUST 只掃候選頁、MUST 帶 kb_version（DL-017 分區）。
"""
from collections.abc import Sequence
from uuid import UUID

import asyncpg
import numpy as np
from anatomy_shared.binary import to_pg_bits

# 256-entry uint8 popcount 查表（一次建好）
_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint16)

_STAGE_B_SQL = """
WITH query_tokens AS (
    SELECT token_idx, q_bits::bit(128) AS q_bin
    FROM unnest($1::text[]) WITH ORDINALITY AS qt(q_bits, token_idx)
),
token_max_per_page AS (
    SELECT pp.page_id, qt.token_idx,
           MAX(128 - (pp.patch_bin <~> qt.q_bin))::float AS sim
    FROM page_patches pp
    JOIN query_tokens qt ON true
    WHERE pp.page_id = ANY($2::uuid[])
      AND pp.kb_version = $3
    GROUP BY pp.page_id, qt.token_idx
)
SELECT page_id, SUM(sim) AS maxsim_score
FROM token_max_per_page
GROUP BY page_id
ORDER BY maxsim_score DESC, page_id ASC
LIMIT $4
"""


async def stage_b_maxsim(
    conn: asyncpg.Connection,
    candidate_page_ids: list[UUID],
    query_tokens_bin: Sequence[bytes],
    kb_version: int,
    top_n: int = 10,
) -> list[tuple[UUID, float]]:
    if not candidate_page_ids:
        return []
    q_bits = [to_pg_bits(t) for t in query_tokens_bin]
    rows = await conn.fetch(_STAGE_B_SQL, q_bits, candidate_page_ids, kb_version, top_n)
    return [(r["page_id"], float(r["maxsim_score"])) for r in rows]


_FETCH_PATCHES_SQL = """
SELECT page_id, patch_bin
FROM page_patches
WHERE page_id = ANY($1::uuid[]) AND kb_version = $2
"""


async def stage_b_maxsim_numpy(
    conn: "asyncpg.Connection",
    candidate_page_ids: list[UUID],
    query_tokens_bin: Sequence[bytes],
    kb_version: int,
    top_n: int = 10,
) -> list[tuple[UUID, float]]:
    """撈候選頁 patch_bin → 應用層 numpy XOR+popcount MaxSim（§4.4 並發退路）。

    K=100 約 100×1024×16B ≈ 1.6MB 傳輸；CPU 從 PG 後端（共享、易爭用）移到 app worker
    （隨 worker 數擴展）。位序與 SQL 路徑一致（皆 16-byte big-endian bit(128)）。
    """
    if not candidate_page_ids:
        return []
    # query tokens → (T, 16) uint8
    q = np.frombuffer(b"".join(query_tokens_bin), dtype=np.uint8).reshape(
        len(query_tokens_bin), 16)
    rows = await conn.fetch(_FETCH_PATCHES_SQL, candidate_page_ids, kb_version)
    # 依 page 聚 patch（bs.bytes = 16 bytes；勿用 bytes(bs)）
    by_page: dict[UUID, list[bytes]] = {}
    for r in rows:
        by_page.setdefault(r["page_id"], []).append(r["patch_bin"].bytes)
    scores: list[tuple[UUID, float]] = []
    for pid, patch_bytes in by_page.items():
        P = np.frombuffer(b"".join(patch_bytes), dtype=np.uint8).reshape(
            len(patch_bytes), 16)                       # (P, 16)
        xor = q[:, None, :] ^ P[None, :, :]             # (T, P, 16)
        dist = _POPCOUNT[xor].sum(axis=2)               # (T, P) Hamming
        sim = 128 - dist                                # (T, P) 相似度
        score = float(sim.max(axis=1).sum())            # Σ_t max_p
        scores.append((pid, score))
    # 次序鍵 page_id：分數並列時輸出決定性（對齊 SQL ORDER BY … page_id ASC）
    scores.sort(key=lambda x: (-x[1], x[0]))
    return scores[:top_n]
