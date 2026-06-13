# ingest/src/anatomy_ingest/writer.py
"""DB 寫入（DL-023 交易語意）：批交易 + 每頁 savepoint + ingest_errors + resume 輔助。

連線由呼叫端建立（asyncpg，連 :6432、statement_cache_size=0）。
編碼/上傳已在交易外完成；本模組只做短交易內的 INSERT。
patch_bin 綁定 to_pg_bits + ::text::bit(128)；pooled 綁定 ::halfvec（§4.4 / DL-019）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
from anatomy_shared.binary import pooled_to_halfvec_literal, to_pg_bits

from .types import EncodedPage, WriteOutcome

logger = logging.getLogger(__name__)


async def ensure_kb_partition(conn, kb_version: int) -> None:
    """建立（冪等）page_patches 的 kb_version 分區（DL-010/DL-017）。

    與 backend.db.kb_version.ensure_kb_partition 同一份 SQL；ingest 自帶以免跨包依賴。
    """
    if type(kb_version) is not int or kb_version < 1:
        raise ValueError(f"kb_version 必須為正整數，收到 {kb_version!r}")
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb_version} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb_version})"
    )


async def completed_page_nums(conn, book_id, kb_version: int) -> set[int]:
    """已成功寫入的 page_num（--resume 用）。"""
    rows = await conn.fetch(
        "SELECT page_num FROM pages WHERE book_id = $1 AND kb_version = $2",
        book_id,
        kb_version,
    )
    return {r["page_num"] for r in rows}


async def _insert_page(conn, book_id, kb_version: int, rec: dict[str, Any], enc: EncodedPage):
    page_id = await conn.fetchval(
        "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
        " pooled, kb_version, embed_model)"
        " VALUES ($1, $2, $3, $4, $5::jsonb, $6::halfvec, $7, $8) RETURNING page_id",
        book_id,
        rec["page_num"],
        rec["page_image_uri"],
        rec["docling_md"],
        json.dumps(rec["metadata"]),
        pooled_to_halfvec_literal(enc.pooled_f32),
        kb_version,
        enc.embed_model,
    )
    await conn.executemany(
        "INSERT INTO page_patches (kb_version, page_id, patch_idx, patch_bin)"
        " VALUES ($1, $2, $3, $4::text::bit(128))",
        [(kb_version, page_id, i, to_pg_bits(b)) for i, b in enumerate(enc.patch_bins)],
    )


async def _record_error(
    conn,
    book_id,
    kb_version: int,
    page_num,
    exc: Exception,
    stage: str = "write",
):
    """寫 ingest_errors。page_num<1（違反 CHECK）改記為 NULL（book 層）以免插入自身失敗。

    stage 可為 parse/render/encode/upload/write（§3.2 CHECK）；供 cli 上游階段共用。
    """
    safe_page = page_num if (isinstance(page_num, int) and page_num >= 1) else None
    await conn.execute(
        "INSERT INTO ingest_errors (kb_version, book_id, page_num, stage, error_type, message,"
        " detail)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
        kb_version,
        book_id,
        safe_page,
        stage,
        type(exc).__name__,
        str(exc)[:2000],
        json.dumps({}),
    )


async def record_page_error(
    conn,
    book_id,
    kb_version: int,
    page_num,
    exc: Exception,
    stage: str,
) -> None:
    """獨立短交易寫 stage-specific ingest_errors（cli 的 encode/upload 階段用）。

    不在 write_batch 交易內——此為獨立短交易（encode/upload 失敗留痕）。
    """
    tx = conn.transaction()
    await tx.start()
    try:
        await _record_error(conn, book_id, kb_version, page_num, exc, stage=stage)
        await tx.commit()
    except Exception as rec_exc:
        await tx.rollback()
        logger.error(
            "寫 ingest_errors 失敗（stage=%s page=%s）：原始=%r 記錯=%r",
            stage,
            page_num,
            exc,
            rec_exc,
        )


async def write_batch(
    conn,
    book_id,
    kb_version: int,
    batch: list[tuple[dict[str, Any], EncodedPage]],
) -> WriteOutcome:
    """一批 → 一交易；每頁 SAVEPOINT。成功 RELEASE、失敗 ROLLBACK TO SAVEPOINT + 寫 ingest_errors。

    批層級致命錯誤（連線斷等）會讓整批交易 rollback 並向上拋（cli 記批層級錯誤、續下批）。
    """
    written: list[int] = []
    failed: list[int] = []
    tx = conn.transaction()
    await tx.start()
    try:
        for idx, (rec, enc) in enumerate(batch):
            sp = f"sp_{idx}"
            await conn.execute(f"SAVEPOINT {sp}")
            # rec 畸形時保持 None → 記為 book 層錯誤、不致整批 rollback（Codex high #2）
            page_num = None
            try:
                # 移進 savepoint 內：畸形 record 的 KeyError 也走逐頁隔離
                page_num = rec["page_num"]
                if enc.page_num != page_num:
                    # 頁面身分守門（Codex high #1）：rec 與 enc 配對錯誤會把 A 頁 metadata
                    # 綁到 B 頁向量，通過所有約束與 sample_verify 卻汙染檢索——視為失敗，不寫入。
                    raise ValueError(
                        f"page 識別不符：rec.page_num={page_num} "
                        f"但 enc.page_num={enc.page_num}（疑似配對錯誤）"
                    )
                enc.validate()
                await _insert_page(conn, book_id, kb_version, rec, enc)
                await conn.execute(f"RELEASE SAVEPOINT {sp}")
                written.append(page_num)
            except Exception as exc:  # 單頁失敗：回退此頁、記錯（自身再包 savepoint）、續下一頁
                await conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                # 記錯包進獨立 savepoint：連寫 ingest_errors 都失敗（如 page_num 違反同一 CHECK）
                # 時 ROLLBACK TO 該 savepoint，**不波及同批已成功的頁**（Codex high #4）。
                esp = f"sp_err_{idx}"
                await conn.execute(f"SAVEPOINT {esp}")
                try:
                    await _record_error(conn, book_id, kb_version, page_num, exc)
                    await conn.execute(f"RELEASE SAVEPOINT {esp}")
                except Exception as rec_exc:
                    await conn.execute(f"ROLLBACK TO SAVEPOINT {esp}")
                    logger.error(
                        "寫 ingest_errors 失敗（page=%s）：原始=%r 記錯=%r",
                        page_num,
                        exc,
                        rec_exc,
                    )
                failed.append(page_num)
        await tx.commit()
    except Exception:
        await tx.rollback()
        raise
    return WriteOutcome(written=written, failed=failed)


async def sample_verify(
    conn,
    book_id,
    kb_version: int,
    fraction: float = 0.05,
    rng_seed: int | None = None,
    expected_page_nums: set[int] | None = None,
) -> dict[str, Any]:
    """§2.7 SHOULD：隨機抽 fraction 比例頁面，比對 pages 存在 + page_patches 計數 > 0。

    expected_page_nums（選填，Codex medium #3）：呼叫端「預期應在庫」的 page_num 集合
    （cli 傳 todo 中未在上游階段失敗的頁）。提供時，**凡 expected 但 pages 缺的頁一律列入
    mismatches**（不受抽樣比例影響）——否則抽樣母體只取既存列，偵測不到「該在卻不在」的遺漏。
    """
    rows = await conn.fetch(
        "SELECT p.page_id, p.page_num, count(pp.patch_idx) AS n"
        " FROM pages p LEFT JOIN page_patches pp"
        "   ON pp.kb_version = p.kb_version AND pp.page_id = p.page_id"
        " WHERE p.book_id = $1 AND p.kb_version = $2"
        " GROUP BY p.page_id, p.page_num ORDER BY p.page_num",
        book_id,
        kb_version,
    )
    mismatches = []
    if expected_page_nums is not None:
        present = {r["page_num"] for r in rows}
        for pn in sorted(set(expected_page_nums) - present):
            mismatches.append({"page_num": pn, "reason": "expected page missing from pages"})
    if not rows:
        return {"sampled": 0, "mismatches": mismatches}
    rng = np.random.default_rng(rng_seed)
    k = max(1, round(len(rows) * fraction))
    idxs = rng.choice(len(rows), size=min(k, len(rows)), replace=False)
    for i in idxs:
        r = rows[int(i)]
        if r["n"] == 0:
            mismatches.append({"page_num": r["page_num"], "reason": "no patches"})
    return {"sampled": len(idxs), "mismatches": mismatches}
