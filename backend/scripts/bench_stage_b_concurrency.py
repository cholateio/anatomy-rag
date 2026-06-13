"""Stage B 並發/p95 benchmark gate（手動；附錄 D.5 硬性驗收）。

用法（需 migrations 已跑、compose 起 postgres+pgbouncer）：
  DATABASE_URL=postgresql://anatomy:***@localhost:6432/anatomy_rag \\
  uv run --no-sync python backend/scripts/bench_stage_b_concurrency.py \\
      [--pages 2000] [--candidates 100] [--concurrency 32] [--pool-size 10] \\
      [--iters 200] [--budget-ms 200]

對 SQL 與 numpy 兩路徑各跑並發負載，回報 p50/p95/max，對預算裁決並建議預設 mode。
生產保真（Codex review #1）：Stage B 在 hnsw_search_txn + savepoint 內跑（pin 連線同生產），
latency 含 pool.acquire/queue 等待，pool 為生產級固定大小（< concurrency → acquire 排隊，
反映 numpy 占用連線做 Python compute 的爭用代價）；各路徑先 warmup 再量測
（兩路徑共用 PG/OS buffer，無法完全隔離 cache）。
以 kb_version=998 建合成資料（跑完清除）；所有連線經 PgBouncer :6432。
單連線探針見 bench_stage_b.py（DL-013）。
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time

import asyncpg
import numpy as np
from anatomy_backend.db.tx_helpers import hnsw_search_txn
from anatomy_backend.retrieval.stage_b import stage_b_maxsim, stage_b_maxsim_numpy

PATCHES_PER_PAGE = 1024
QUERY_TOKENS = 20
BENCH_KB = 998


def _rand_bits(rng) -> asyncpg.BitString:
    return asyncpg.BitString.frombytes(rng.bytes(16), bitlength=128)


async def seed(conn, n_pages):
    rng = np.random.default_rng(0)
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{BENCH_KB} "
        f"PARTITION OF page_patches FOR VALUES IN ({BENCH_KB})")
    book_id = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('bench-conc') RETURNING book_id")
    pooled = "[" + ",".join("0.01" for _ in range(128)) + "]"
    page_ids = []
    for i in range(n_pages):
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1,$2,'bench','bench','{}'::jsonb,$3::halfvec,$4,'bench')"
            " RETURNING page_id", book_id, i + 1, pooled, BENCH_KB)
        page_ids.append(pid)
        recs = [(BENCH_KB, pid, j, _rand_bits(rng)) for j in range(PATCHES_PER_PAGE)]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
        if (i + 1) % 200 == 0:
            print(f"  seeded {i + 1}/{n_pages}")
    return book_id, page_ids


async def _run_path(pool, fn, page_ids, n_cand, iters, concurrency, rng):
    """模型生產 Stage B：在 hnsw_search_txn + savepoint 內跑（pin 連線、同生產 txn 生命週期），
    latency 從 pool.acquire 之前起算（含 queue 等待）。offered concurrency = burst；
    pool < concurrency 時於 acquire 排隊——numpy 占用連線做 Python compute 的代價反映在
    p95（Codex review #1）。"""
    sem = asyncio.Semaphore(concurrency)
    latencies = []

    async def one():
        cand = [page_ids[i] for i in rng.choice(len(page_ids), n_cand, replace=False)]
        tokens = [rng.bytes(16) for _ in range(QUERY_TOKENS)]
        async with sem:
            t0 = time.perf_counter()                      # 含 acquire/queue 等待
            async with hnsw_search_txn(pool) as conn:     # 同生產：pin 連線 + SET LOCAL
                async with conn.transaction():            # 同生產 Stage B savepoint
                    await fn(conn, cand, tokens, BENCH_KB, 10)
            latencies.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*[one() for _ in range(iters)])
    latencies.sort()
    return {
        "p50_ms": round(statistics.median(latencies), 1),
        "p95_ms": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 1),
        "max_ms": round(latencies[-1], 1),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2000)
    ap.add_argument("--candidates", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=32)   # offered burst（同時在途請求）
    ap.add_argument("--pool-size", type=int, default=10)     # 生產池大小（應對齊部署值）
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--budget-ms", type=int, default=200)
    args = ap.parse_args()

    # 生產級固定池；pool < concurrency → 多餘請求於 acquire 排隊（模型 PgBouncer 占用爭用）
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"], statement_cache_size=0,
        min_size=args.pool_size, max_size=args.pool_size)

    # leftover 守門：偵測殘留 bench 資料就 fail-fast，且**不刪**（留給人工檢查）；
    # 置於 cleanup try 之外，避免 finally 誤刪他人/前次殘留。
    seed_conn = await pool.acquire()
    leftover = await seed_conn.fetchval(
        "SELECT count(*) FROM pages WHERE kb_version = $1", BENCH_KB)
    if leftover:
        await pool.release(seed_conn)
        await pool.close()
        print(f"錯誤：殘留 bench 資料（kb_version={BENCH_KB}：{leftover} 列），請先清理")
        sys.exit(1)

    # 過守門後 kb_version=998 由本次 run 獨佔 → 單一 try/finally；finally 無條件清理
    # （即使 seed 中途失敗也清掉部分資料、釋放連線、關池）。
    book_id = None
    try:
        print(f"seeding {args.pages} pages × {PATCHES_PER_PAGE}（首次約數分鐘）…")
        book_id, page_ids = await seed(seed_conn, args.pages)
        await pool.release(seed_conn)
        seed_conn = None

        n_cand = min(args.candidates, len(page_ids))
        rng = np.random.default_rng(1)

        # 各路徑先 warmup 再量測（注意：兩路徑共用 PG/OS buffer，無法完全隔離 cache；
        # 偏差利於較慢的 numpy，不影響 SQL 勝出結論）
        await _run_path(pool, stage_b_maxsim, page_ids, n_cand, args.concurrency,
                        args.concurrency, rng)
        sql = await _run_path(pool, stage_b_maxsim, page_ids, n_cand, args.iters,
                              args.concurrency, rng)
        await _run_path(pool, stage_b_maxsim_numpy, page_ids, n_cand, args.concurrency,
                        args.concurrency, rng)
        npy = await _run_path(pool, stage_b_maxsim_numpy, page_ids, n_cand, args.iters,
                              args.concurrency, rng)

        recommend = "sql" if sql["p95_ms"] <= args.budget_ms else (
            "numpy" if npy["p95_ms"] <= args.budget_ms else "neither")
        report = {
            "pages": args.pages, "candidates": n_cand, "concurrency": args.concurrency,
            "pool_size": args.pool_size, "iters": args.iters, "budget_ms": args.budget_ms,
            "sql": sql, "numpy": npy, "recommended_stage_b_mode": recommend,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if recommend == "neither":
            print("GATE FAIL：兩路徑 p95 皆超預算——需 INT8 rescore / VectorChord（Phase 12）"
                  " 或調 K / efSearch；見 §4.4 / §4.6")
            sys.exit(2)
        print(f"GATE PASS：建議 production stage_b_mode='{recommend}'")
    finally:
        if seed_conn is not None:
            await pool.release(seed_conn)
        print("cleaning up…")
        async with pool.acquire() as c:
            await c.execute(f"DROP TABLE IF EXISTS page_patches_v{BENCH_KB}")
            await c.execute("DELETE FROM pages WHERE kb_version = $1", BENCH_KB)
            if book_id is not None:
                await c.execute("DELETE FROM books WHERE book_id = $1", book_id)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
