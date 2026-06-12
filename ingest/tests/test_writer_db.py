# ingest/tests/test_writer_db.py
"""writer 交易語意（DL-023）：批交易 + 每頁 savepoint + ingest_errors + resume。

前置：schema 已由 backend Alembic migrate（CI db-integration 已 upgrade head）。
每個測試自建一本 book、用唯一 kb_version 避免相互污染，結束清理。
"""
import os

import asyncpg
import numpy as np
import pytest
from anatomy_ingest.types import EncodedPage
from anatomy_ingest.writer import (
    completed_page_nums,
    ensure_kb_partition,
    sample_verify,
    write_batch,
)

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

KB = 9001  # 測試專用 kb_version（避開正式 1）


def _enc(page_num, n_patches=4):
    rng = np.random.default_rng(page_num)
    bins = [
        np.packbits((rng.standard_normal(128) > 0).astype("uint8")).tobytes()
        for _ in range(n_patches)
    ]
    return EncodedPage(
        page_num=page_num,
        patch_bins=bins,
        pooled_f32=rng.standard_normal(128).astype("float32"),
        embed_model="mock-colpali",
    )


def _page_record(page_num):
    return {
        "page_num": page_num,
        "page_image_uri": f"s3://b/kb_v{KB}/page_{page_num:04d}.png",
        "docling_md": f"## Chapter\n\npage {page_num}",
        "metadata": {
            "page_num": page_num,
            "anatomy_system": "musculoskeletal",
            "page_type": "mixed",
            "figures": [],
        },
    }


async def _conn():
    return await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)


async def _new_book(conn):
    return await conn.fetchval(
        "INSERT INTO books (title, edition) VALUES ($1, $2) RETURNING book_id",
        "Writer Test",
        "1",
    )


@pytest.fixture
async def conn():
    c = await _conn()
    await ensure_kb_partition(c, KB)
    yield c
    # 清理：刪本測試 kb_version 的資料
    await c.execute("DELETE FROM page_patches WHERE kb_version = $1", KB)
    await c.execute("DELETE FROM pages WHERE kb_version = $1", KB)
    await c.execute("DELETE FROM ingest_errors WHERE kb_version = $1", KB)
    await c.close()


async def test_write_batch_happy_path(conn):
    book_id = await _new_book(conn)
    batch = [(_page_record(1), _enc(1)), (_page_record(2), _enc(2))]
    outcome = await write_batch(conn, book_id, KB, batch)
    assert sorted(outcome.written) == [1, 2] and outcome.failed == []
    n_pages = await conn.fetchval("SELECT count(*) FROM pages WHERE kb_version=$1", KB)
    n_patches = await conn.fetchval(
        "SELECT count(*) FROM page_patches WHERE kb_version=$1", KB
    )
    assert n_pages == 2 and n_patches == 8  # 2 頁 × 4 patch


async def test_savepoint_isolates_failed_page(conn):
    book_id = await _new_book(conn)
    good = (_page_record(1), _enc(1))
    # page 2 故意違反 UNIQUE(book_id,page_num,kb_version)：先塞一筆同 page_num
    await write_batch(conn, book_id, KB, [(_page_record(2), _enc(2))])
    dup = (_page_record(2), _enc(2))  # 重複 → 寫入失敗
    good3 = (_page_record(3), _enc(3))
    outcome = await write_batch(conn, book_id, KB, [good, dup, good3])
    assert sorted(outcome.written) == [1, 3]
    assert outcome.failed == [2]
    # ingest_errors 有 page 2 的 write 失敗紀錄
    err = await conn.fetchrow(
        "SELECT stage, page_num FROM ingest_errors WHERE kb_version=$1 AND page_num=2", KB
    )
    assert err["stage"] == "write"
    # 交易仍提交：page 1、3 在庫
    rows = await conn.fetch(
        "SELECT page_num FROM pages WHERE kb_version=$1 ORDER BY page_num", KB
    )
    assert [r["page_num"] for r in rows] == [2, 3, 1] or {
        r["page_num"] for r in rows
    } == {1, 2, 3}


async def test_completed_page_nums_for_resume(conn):
    book_id = await _new_book(conn)
    await write_batch(
        conn, book_id, KB, [(_page_record(1), _enc(1)), (_page_record(5), _enc(5))]
    )
    done = await completed_page_nums(conn, book_id, KB)
    assert done == {1, 5}


async def test_sample_verify_counts_match(conn):
    book_id = await _new_book(conn)
    batch = [(_page_record(i), _enc(i, n_patches=4)) for i in (1, 2, 3, 4)]
    await write_batch(conn, book_id, KB, batch)
    # 全抽（fraction=1.0）：每頁 patch 數應為 4
    report = await sample_verify(conn, book_id, KB, fraction=1.0, rng_seed=0)
    assert report["sampled"] == 4 and report["mismatches"] == []


async def test_record_error_failure_does_not_lose_batch(conn, monkeypatch):
    """記錯本身爆炸時，獨立 savepoint 保護同批已成功頁不被整批 rollback（Codex high #4）。"""
    import anatomy_ingest.writer as w

    book_id = await _new_book(conn)
    await write_batch(
        conn, book_id, KB, [(_page_record(2), _enc(2))]
    )  # 先塞 page 2 → 後續重複觸發失敗

    async def boom(*a, **k):
        raise RuntimeError("ingest_errors 寫入爆炸")

    monkeypatch.setattr(w, "_record_error", boom)
    batch = [
        (_page_record(1), _enc(1)),
        (_page_record(2), _enc(2)),
        (_page_record(3), _enc(3)),
    ]
    outcome = await write_batch(conn, book_id, KB, batch)
    assert sorted(outcome.written) == [1, 3] and outcome.failed == [2]
    rows = await conn.fetch(
        "SELECT page_num FROM pages WHERE kb_version=$1 AND book_id=$2", KB, book_id
    )
    assert {r["page_num"] for r in rows} == {1, 2, 3}  # 1、3 仍提交，未因記錯爆炸而整批丟失


async def test_record_error_clamps_invalid_page_num_to_null(conn):
    """page_num<1 違反 pages CHECK → 記錯時 clamp 為 NULL（book 層），ingest_errors 插入不自爆。"""
    book_id = await _new_book(conn)
    bad = dict(_page_record(1))
    bad["page_num"] = -5  # 違反 pages CHECK(page_num>=1)
    outcome = await write_batch(
        conn, book_id, KB, [(bad, _enc(1)), (_page_record(7), _enc(7))]
    )
    assert outcome.failed == [-5] and outcome.written == [7]
    err = await conn.fetchrow(
        "SELECT page_num, stage FROM ingest_errors WHERE kb_version=$1 AND book_id=$2"
        " ORDER BY error_id DESC LIMIT 1",
        KB,
        book_id,
    )
    assert err["page_num"] is None and err["stage"] == "write"
