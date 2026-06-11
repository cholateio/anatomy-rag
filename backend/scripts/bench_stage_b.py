"""Stage B MaxSim SQL 延遲初步量測（手動執行；DL-013 探針，非 CI gate）。

用法（需 migrations 已跑、compose 起 postgres+pgbouncer）：
  DATABASE_URL=postgresql://anatomy:***@localhost:6432/anatomy_rag \\
  PG_DIRECT_URL=postgresql://anatomy:***@localhost:5432/anatomy_rag \\
  uv run --no-sync python backend/scripts/bench_stage_b.py [--pages 2000] [--candidates 100]

以 kb_version=999 建合成資料（跑完清除）；asyncpg.BitString 走 COPY 快速灌入。
單連線 microbenchmark：只回答「SQL 聚合本體量級」，不代表並發/真實資料 p95。
"""
import argparse
import asyncio
import json
import os
import statistics
import time
import uuid

import asyncpg
import numpy as np

PATCHES_PER_PAGE = 1024
QUERY_TOKENS = 18
BENCH_KB = 999

STAGE_B_SQL = """
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
ORDER BY maxsim_score DESC
LIMIT 10
"""


def _rand_bits(rng) -> asyncpg.BitString:
    return asyncpg.BitString.frombytes(rng.bytes(16), bitlength=128)


async def seed(conn, n_pages: int) -> list[uuid.UUID]:
    rng = np.random.default_rng(0)
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS page_patches_v999 "
        "PARTITION OF page_patches FOR VALUES IN (999)"
    )
    book_id = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('bench-only') RETURNING book_id"
    )
    page_ids = []
    for i in range(n_pages):
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1, $2, 'bench', 'bench', '{}'::jsonb, $3::halfvec, $4, 'bench')"
            " RETURNING page_id",
            book_id, i, "[" + ",".join("0.01" for _ in range(128)) + "]", BENCH_KB,
        )
        page_ids.append(pid)
        records = [(BENCH_KB, pid, j, _rand_bits(rng)) for j in range(PATCHES_PER_PAGE)]
        await conn.copy_records_to_table(
            "page_patches", records=records,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"],
        )
        if (i + 1) % 200 == 0:
            print(f"  seeded {i + 1}/{n_pages} pages")
    return page_ids


async def cleanup(conn):
    await conn.execute("DROP TABLE IF EXISTS page_patches_v999")
    await conn.execute("DELETE FROM pages WHERE kb_version = $1", BENCH_KB)
    await conn.execute("DELETE FROM books WHERE title = 'bench-only'")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2000)
    ap.add_argument("--candidates", type=int, default=100)
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=5)
    args = ap.parse_args()

    rng = np.random.default_rng(1)
    direct = await asyncpg.connect(os.environ["PG_DIRECT_URL"], statement_cache_size=0)
    pooled = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        print(f"seeding {args.pages} pages × {PATCHES_PER_PAGE} patches（首次約數分鐘）…")
        page_ids = await seed(direct, args.pages)

        def make_query():
            cand = list(rng.choice(np.array(page_ids), size=args.candidates, replace=False))
            tokens = ["".join(f"{b:08b}" for b in rng.bytes(16)) for _ in range(QUERY_TOKENS)]
            return tokens, cand

        for _ in range(args.warmup):  # 排除冷 cache / plan 首跑的離群值
            tokens, cand = make_query()
            await pooled.fetch(STAGE_B_SQL, tokens, cand, BENCH_KB)

        latencies = []
        for _ in range(args.iters):
            tokens, cand = make_query()
            t0 = time.perf_counter()
            rows = await pooled.fetch(STAGE_B_SQL, tokens, cand, BENCH_KB)
            latencies.append((time.perf_counter() - t0) * 1000)
            assert len(rows) == 10
        latencies.sort()
        report = {
            "pages": args.pages, "candidates": args.candidates,
            "tokens": QUERY_TOKENS, "patches_per_page": PATCHES_PER_PAGE,
            "iters": args.iters,
            "p50_ms": round(statistics.median(latencies), 1),
            "p95_ms": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 1),
            "max_ms": round(latencies[-1], 1),
            "budget_ms": 200,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            "（單連線 microbenchmark、合成隨機 bits：只能回答『SQL 聚合本體量級』，"
            "不代表並發/真實資料 p95——DL-013 預算 200ms 的正式 gate 留 Phase 5；"
            "未達標 → 評估應用層 numpy MaxSim 退路，§4.4）"
        )
    finally:
        print("cleaning up bench data…")
        await cleanup(direct)
        await direct.close()
        await pooled.close()


if __name__ == "__main__":
    asyncio.run(main())
