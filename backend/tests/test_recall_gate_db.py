import os
import uuid

import asyncpg
import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.orchestrator import retrieve
from anatomy_backend.retrieval.query_repr import QueryRepr
from anatomy_eval.golden import GoldenQA
from anatomy_eval.harness import evaluate_recall_by_class

pytestmark = pytest.mark.db
KB = 8


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


async def test_recall_gate_plumbing(pool):
    """植入式語料：每題目標頁的 patches = 該題 query tokens → MaxSim 必中。"""
    rng = np.random.default_rng(31)
    # 20 頁 distractor + 3 頁目標（text_only / figure_id / cross_page 各一）
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute(
                f"CREATE TABLE page_patches_v{KB} PARTITION OF page_patches "
                f"FOR VALUES IN ({KB})")
            book = await conn.fetchval(
                "INSERT INTO books (title) VALUES ('recall') RETURNING book_id")

            async def add_page(num, md, patches):
                pv = "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"
                pid = await conn.fetchval(
                    "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md,"
                    " metadata, pooled, kb_version, embed_model)"
                    " VALUES ($1,$2,'s3://x',$3,'{}'::jsonb,$4::halfvec,$5,'m')"
                    " RETURNING page_id", book, num, md, pv, KB)
                recs = [(KB, pid, j, asyncpg.BitString.frombytes(p, bitlength=128))
                        for j, p in enumerate(patches)]
                await conn.copy_records_to_table(
                    "page_patches", records=recs,
                    columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
                return pid

            for n in range(20):
                await add_page(n + 1, f"distractor page {n}",
                               [rng.bytes(16) for _ in range(8)])
            targets = {}
            specs = [("q_text", "biceps brachii origin", "text_only"),
                     ("q_fig", "deltoid muscle figure", "figure_id"),
                     ("q_cross", "brachial plexus pathway", "cross_page")]
            qreprs = {}
            for i, (qid, text, _cat) in enumerate(specs):
                patches = [rng.bytes(16) for _ in range(8)]
                pid = await add_page(100 + i, text, patches)
                targets[qid] = pid
                qreprs[qid] = QueryRepr(
                    pooled_f32=tuple(float(x) for x in rng.standard_normal(128)),
                    tokens_bin=tuple(patches[:6]),
                    translated_q=text, lang="zh")

        golden = [
            GoldenQA(id=qid, category=cat, query=text,
                     expected_pages=(str(targets[qid]),))
            for (qid, text, cat) in specs
        ]

        async def _retrieve_ids(qa: GoldenQA):
            res = await retrieve(pool, qa.query, qreprs[qa.id], None,
                                 kb_version=KB, top_n=3)
            return [str(r.page_id) for r in res]

        id_map = {qa.id: await _retrieve_ids(qa) for qa in golden}
        report = evaluate_recall_by_class(golden, lambda qa: id_map[qa.id], k=3)
        print(report)
        assert report["overall"] == 1.0
        assert report["by_class"]["text_only"] == 1.0
        assert report["by_class"]["figure_id"] == 1.0
        assert report["by_class"]["cross_page"] == 1.0
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
