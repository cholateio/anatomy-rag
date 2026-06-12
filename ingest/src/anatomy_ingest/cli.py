# ingest/src/anatomy_ingest/cli.py
"""離線建庫 CLI（§2.6）。MUST NOT 呼叫任何雲端 LLM API（離線紅線；test_no_cloud_llm 守門）。

流程（每批）：來源頁 → encode（GPU/mock，交易外，逐頁 guard）→ 上傳 PNG（交易外，逐頁 guard）
→ write_batch（短交易，'write' 階段 savepoint）。各上游階段（render/encode/upload）失敗逐頁寫
stage-specific ingest_errors 並續跑（§2.7）。書本識別走顯式 --book-id（§2.6 重建/續跑），
不靠 title 猜測。
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
from typing import Any


def _positive_int(value: str) -> int:
    """argparse type：正整數（>=1）；0/負/非整數 → argparse 退碼 2（Codex medium #8）。"""
    try:
        iv = int(value)
    except (TypeError, ValueError) as e:
        raise argparse.ArgumentTypeError(f"須為整數，收到 {value!r}") from e
    if iv < 1:
        raise argparse.ArgumentTypeError(f"須為正整數（>=1），收到 {iv}")
    return iv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anatomy_ingest.cli", description="離線建庫管線")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", help="教科書 PDF 路徑（真實路徑，需 poppler + docling）")
    src.add_argument("--synthetic", type=_positive_int, metavar="N",
                     help="合成 N 頁（dev/CI，無 poppler/GPU）")
    p.add_argument("--book-meta", required=True, help="書籍 metadata YAML")
    p.add_argument("--kb-version", type=_positive_int, required=True)
    p.add_argument("--batch-size", type=_positive_int, default=8)
    p.add_argument("--book-id", default=None,
                   help="既有 book_id（UUID）：重建（無 --resume：先刪該 book+kb_version 既有頁）"
                        "或續跑（--resume：跳過已完成頁）。首次建庫不帶此旗標→新增一本書。")
    p.add_argument("--resume", action="store_true",
                   help="跳過 pages 已存在的頁（須搭 --book-id；不靠 title 猜書）")
    p.add_argument("--mock-encoder", action="store_true", help="用決定性 mock runtime（CI/無 GPU）")
    return p


def chunk_pages(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def plan_pages(pages, completed: set[int]):
    todo = [sp for sp in pages if sp.parse.page_num not in completed]
    skipped = sorted(sp.parse.page_num for sp in pages if sp.parse.page_num in completed)
    return todo, skipped


def _load_book_meta(path: str) -> dict[str, Any]:
    import yaml  # lazy

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def _resolve_book(conn, ns, book_meta: dict[str, Any]):
    """書本識別（§2.6，Codex high #1 修正版）：

    - 帶 --book-id：書須存在。無 --resume＝需重建（needs_rebuild=True）→ **不在此刪除**，
      由 _run 在來源解析成功後才刪（避免 PDF 解析失敗時舊版本已被清空）；
      --resume → needs_rebuild=False，不刪、供跳過已完成頁。
    - 不帶 --book-id：--resume 為非法（不靠 title 猜書）；否則新增一本書（首次建庫）。
    回傳 (book_uuid, needs_rebuild: bool)。
    """
    if ns.book_id:
        import uuid

        try:
            book_uuid = uuid.UUID(str(ns.book_id))
        except ValueError as e:
            raise SystemExit(f"--book-id 非合法 UUID：{ns.book_id!r}") from e
        exists = await conn.fetchval("SELECT 1 FROM books WHERE book_id = $1", book_uuid)
        if not exists:
            raise SystemExit(f"--book-id {ns.book_id} 不存在；首次建庫請不帶 --book-id")
        needs_rebuild = not ns.resume
        return book_uuid, needs_rebuild
    if ns.resume:
        raise SystemExit("--resume 須搭配 --book-id（不靠 title 猜書，避免續跑到錯的書/版本）")
    book_uuid = await conn.fetchval(
        "INSERT INTO books (title, edition, isbn) VALUES ($1, $2, $3) RETURNING book_id",
        book_meta.get("book_title") or "Untitled",
        str(book_meta.get("edition") or ""), book_meta.get("isbn"),
    )
    print(f"[new] 新增書本 book_id={book_uuid}")
    return book_uuid, False


async def _encode_and_upload_batch(runtime, s3, cfg, book_id, kb_version, conn, batch_pages):
    """逐頁 encode + upload，各階段失敗寫 stage-specific ingest_errors 並續跑（Codex high #1/#2）。

    回 (records, failed_page_nums)：records 為通過 encode+upload、待 write_batch 的 (rec, enc)。
    """
    from .colpali_encoder import encode_page_image
    from .storage import page_key, upload_page_png
    from .writer import record_page_error

    records, failed = [], []
    for sp in batch_pages:
        pn = sp.parse.page_num
        # FIX C（Codex high #5）：Docling 未解析此頁（parse_failed 佔位符）→ 記 parse 失敗，跳過
        if sp.parse.metadata.get("parse_failed"):
            err = RuntimeError("Docling 未解析此頁")
            await record_page_error(conn, book_id, kb_version, pn, err, "parse")
            failed.append(pn)
            continue
        if sp.image is None:  # 渲染缺頁（pdf_source 未丟棄）→ 記 render 失敗，跳過
            err = RuntimeError("render 缺頁影像")
            await record_page_error(conn, book_id, kb_version, pn, err, "render")
            failed.append(pn)
            continue
        try:
            enc = encode_page_image(runtime, sp.image)
            enc = dataclasses.replace(enc, page_num=pn)
        except Exception as exc:
            await record_page_error(conn, book_id, kb_version, pn, exc, "encode")
            failed.append(pn)
            continue
        try:
            key = page_key(kb_version, str(book_id), pn)
            uri = upload_page_png(s3, cfg.s3_bucket, key, sp.image)
        except Exception as exc:
            await record_page_error(conn, book_id, kb_version, pn, exc, "upload")
            failed.append(pn)
            continue
        records.append(({
            "page_num": pn,
            "page_image_uri": uri,
            "docling_md": sp.parse.markdown,
            "metadata": sp.parse.metadata,
        }, enc))
    return records, failed


async def _run(ns: argparse.Namespace) -> int:
    import asyncpg
    from anatomy_shared.colpali_runtime import get_runtime

    from .config import IngestConfig
    from .source import pdf_source, synthetic_source
    from .writer import (
        completed_page_nums,
        ensure_kb_partition,
        record_page_error,
        sample_verify,
        write_batch,
    )

    cfg = IngestConfig.from_env()
    book_meta = _load_book_meta(ns.book_meta)
    runtime = get_runtime(mock=ns.mock_encoder or bool(ns.synthetic))
    s3 = cfg.make_s3_client()
    conn = await asyncpg.connect(cfg.database_url, statement_cache_size=0)
    try:
        await ensure_kb_partition(conn, ns.kb_version)
        book_id, needs_rebuild = await _resolve_book(conn, ns, book_meta)

        # 來源段（parse/render 為整檔操作）：整檔失敗記 book 層 parse 錯誤、非零退出（§2.7）
        try:
            if ns.synthetic:
                pages = list(synthetic_source(ns.synthetic, book_meta))
            else:
                pages = list(pdf_source(ns.pdf, book_meta))
        except Exception as exc:
            await record_page_error(conn, book_id, ns.kb_version, None, exc, "parse")
            print(f"[fatal] 來源解析/渲染失敗：{exc!r}（已記 ingest_errors stage=parse）")
            return 1

        # FIX A（Codex high #1）：來源解析成功後才刪舊頁，避免 PDF 失敗時舊版本已被清空
        if needs_rebuild:
            await conn.execute(
                "DELETE FROM pages WHERE book_id = $1 AND kb_version = $2",
                book_id, ns.kb_version,
            )
            print(f"[rebuild] 已刪除 book={book_id} kb_version={ns.kb_version} 既有頁，重建")

        completed = await completed_page_nums(conn, book_id, ns.kb_version) if ns.resume else set()
        todo, skipped = plan_pages(pages, completed)
        if skipped:
            print(f"[resume] 跳過已完成 {len(skipped)} 頁：{skipped}")

        total_written, total_failed = [], []
        for batch_pages in chunk_pages(todo, ns.batch_size):
            records, up_failed = await _encode_and_upload_batch(
                runtime, s3, cfg, book_id, ns.kb_version, conn, batch_pages)
            total_failed += up_failed
            outcome = await write_batch(conn, book_id, ns.kb_version, records)
            total_written += outcome.written
            total_failed += outcome.failed
            print(f"[batch] 寫入 {outcome.written}，失敗 寫={outcome.failed} 上游={up_failed}")

        # expected = 本次嘗試（todo）中未在上游階段失敗的頁；sample_verify 據此偵測「該在卻不在」
        expected = {sp.parse.page_num for sp in todo} - set(total_failed)
        report = await sample_verify(conn, book_id, ns.kb_version, fraction=0.05,
                                     expected_page_nums=expected)
        print(
            f"[done] 共寫入 {len(total_written)} 頁、失敗 {len(total_failed)} 頁；"
            f"抽樣校驗 {report}"
        )
        return 1 if (total_failed or report["mismatches"]) else 0
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    return asyncio.run(_run(ns))


if __name__ == "__main__":
    sys.exit(main())
