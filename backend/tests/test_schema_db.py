"""schema 行為整合測試（真 Postgres，經 PgBouncer :6432）。"""
import json
import uuid

import numpy as np
import pytest
from anatomy_backend.db.kb_version import ensure_kb_partition
from anatomy_shared.binary import binarize, hamming_distance, to_pg_bits

pytestmark = pytest.mark.db

BOOK_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _vec_text(seed: int) -> str:
    rng = np.random.default_rng(seed)
    return "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"


async def _seed_book_and_page(conn, page_num=1, kb_version=1, md="biceps brachii origin"):
    await conn.execute(
        "INSERT INTO books (book_id, title) VALUES ($1, 'Gray''s Anatomy') "
        "ON CONFLICT (book_id) DO NOTHING",
        BOOK_ID,
    )
    return await conn.fetchval(
        "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
        " pooled, kb_version, embed_model)"
        " VALUES ($1, $2, 's3://x.png', $3, $4::jsonb, $5::halfvec, $6, 'colpali-v1.3-hf')"
        " RETURNING page_id",
        BOOK_ID, page_num, md, json.dumps({"page_type": "pure_text"}),
        _vec_text(page_num), kb_version,
    )


async def test_partition_routing_per_kb_version(clean_db):
    conn = clean_db
    await ensure_kb_partition(conn, 1)
    await ensure_kb_partition(conn, 2)
    p1 = await _seed_book_and_page(conn, page_num=1, kb_version=1)
    p2 = await _seed_book_and_page(conn, page_num=1, kb_version=2)
    tok = binarize(np.random.default_rng(0).standard_normal(128))
    await conn.execute(
        "INSERT INTO page_patches VALUES (1, $1, 0, $2::text::bit(128))", p1, to_pg_bits(tok)
    )
    await conn.execute(
        "INSERT INTO page_patches VALUES (2, $1, 0, $2::text::bit(128))", p2, to_pg_bits(tok)
    )
    rows = await conn.fetch(
        "SELECT kb_version, tableoid::regclass::text AS part FROM page_patches ORDER BY kb_version"
    )
    assert [(r["kb_version"], r["part"]) for r in rows] == [
        (1, "page_patches_v1"), (2, "page_patches_v2"),
    ]


async def test_insert_without_partition_fails_fast(clean_db):
    conn = clean_db
    pid = await _seed_book_and_page(conn, page_num=9, kb_version=3)  # pages 不分區，可插
    import asyncpg

    # v3 分區未建：PostgreSQL 拋 SQLSTATE 23514（no partition of relation … found for row）
    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await conn.execute(
            "INSERT INTO page_patches VALUES (3, $1, 0, $2::text::bit(128))",
            pid, "0" * 128,
        )
    assert exc.value.sqlstate == "23514"


async def test_fk_rejects_cross_version_mismatch(clean_db):
    """複合 FK：patch 的 (kb_version, page_id) 必須整組存在於 pages——
    防止 v1 patch 指到 v2 page 後被路由進錯誤分區而靜默漏檢。"""
    conn = clean_db
    await ensure_kb_partition(conn, 1)
    await ensure_kb_partition(conn, 2)
    pid_v2 = await _seed_book_and_page(conn, page_num=5, kb_version=2)
    import asyncpg

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(
            "INSERT INTO page_patches VALUES (1, $1, 0, $2::text::bit(128))", pid_v2, "1" * 128
        )


async def test_fk_cascade_deletes_patches(clean_db):
    conn = clean_db
    await ensure_kb_partition(conn, 1)
    pid = await _seed_book_and_page(conn, page_num=2, kb_version=1)
    await conn.execute(
        "INSERT INTO page_patches VALUES (1, $1, 0, $2::text::bit(128))", pid, "1" * 128
    )
    await conn.execute("DELETE FROM pages WHERE page_id = $1", pid)
    assert await conn.fetchval("SELECT count(*) FROM page_patches WHERE page_id=$1", pid) == 0


async def test_hamming_operator_matches_shared_oracle(db_conn):
    """SQL `<~>` 必須與 shared/binary.hamming_distance 一致（位序約定 to_pg_bits 單一來源）。"""
    rng = np.random.default_rng(42)
    for _ in range(5):
        a = binarize(rng.standard_normal(128))
        b = binarize(rng.standard_normal(128))
        sql_dist = await db_conn.fetchval(
            "SELECT $1::text::bit(128) <~> $2::text::bit(128)", to_pg_bits(a), to_pg_bits(b)
        )
        assert int(sql_dist) == hamming_distance(a, b)
