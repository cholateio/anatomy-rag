# ingest/scripts/ingest_gate.py
"""手動 GPU gate：真 3 頁 PDF → real ColPali → 真 MinIO/PG 端到端建庫驗收（非 CI）。

前置：poppler 已裝、GPU venv 有 torch+colpali、make up + make migrate 已跑、.env 指 localhost。
產生 3 頁可區辨 PDF（PIL），走完整 pdf_source（docling+poppler+real runtime）→ 寫 kb_version=9000。
驗收（Codex medium #6）：
- pages.page_num 集合 == {1,2,3}（解析/渲染/DB 頁碼精確對應，抓 docling/pdf2image 頁碼漂移）
- 每頁 page_patches>0、embed_model=vidore/colpali-v1.3-hf
- **逐頁 GET MinIO 物件**確認存在且為 PNG（非只數 DB 列）
完成後清理該 kb_version + book。
"""
import asyncio
import io
import os
import tempfile

import asyncpg
from anatomy_ingest.cli import _run, build_parser  # 重用編排
from anatomy_ingest.config import IngestConfig
from PIL import Image, ImageDraw

KB = 9000
N_PAGES = 3


def _make_pdf(path: str):
    pages = []
    chapters = ["Upper Limb", "The Heart", "Cranial Nerves"]
    for i, chap in enumerate(chapters, start=1):
        img = Image.new("RGB", (1240, 1754), "white")  # ~150dpi A4
        d = ImageDraw.Draw(img)
        d.text((80, 80), f"Chapter: {chap}", fill="black")
        d.text((80, 140), f"This is distinguishable page {i}. See Fig. {i}-1.", fill="black")
        pages.append(img)
    pages[0].save(path, "PDF", resolution=200.0, save_all=True, append_images=pages[1:])


async def main():
    os.environ.setdefault("DATABASE_URL", "postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag")
    os.environ.setdefault("S3_ENDPOINT", "http://localhost:9000")
    os.environ.setdefault("S3_BUCKET", "anatomy-rag-pages")
    os.environ.setdefault("S3_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("S3_SECRET_KEY", "minioadmin")
    cfg = IngestConfig.from_env()

    # FIX B（Codex high #3）：fail-fast — kb_version=9000 若已有資料代表上次清理不完全
    _preflight_conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        existing = await _preflight_conn.fetchval(
            "SELECT count(*) FROM pages WHERE kb_version=$1", KB
        )
    finally:
        await _preflight_conn.close()
    if existing > 0:
        raise SystemExit(f"gate kb_version {KB} 已有資料（{existing} 頁），請先清理")

    with tempfile.TemporaryDirectory() as td:
        pdf = os.path.join(td, "gate.pdf")
        meta = os.path.join(td, "gate.yaml")
        _make_pdf(pdf)
        with open(meta, "w") as f:
            f.write("book_title: Gate Atlas\nedition: 1\n")
        ns = build_parser().parse_args(
            ["--pdf", pdf, "--book-meta", meta, "--kb-version", str(KB), "--batch-size", "2"]
        )
        rc = await _run(ns)

    # FIX B（Codex high #3）：ingest 失敗立即中止，不跑驗證查詢
    assert rc == 0, f"ingest 失敗 rc={rc}"

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    s3 = cfg.make_s3_client()
    try:
        rows = await conn.fetch(
            "SELECT page_num, page_image_uri, embed_model,"
            " (SELECT count(*) FROM page_patches pp WHERE pp.kb_version=p.kb_version"
            "  AND pp.page_id=p.page_id) AS n_patches"
            " FROM pages p WHERE kb_version=$1 ORDER BY page_num", KB)
        page_nums = [r["page_num"] for r in rows]
        print(f"[gate] rc={rc} page_nums={page_nums}")
        assert page_nums == list(range(1, N_PAGES + 1)), f"頁碼對應不符：{page_nums}"
        for r in rows:
            assert r["n_patches"] > 0, f"page {r['page_num']} 無 patch"
            assert r["embed_model"] == "vidore/colpali-v1.3-hf", "embed_model 應為真實模型"
            # 逐頁 GET MinIO 物件並驗 PNG
            key = r["page_image_uri"].split(f"{cfg.s3_bucket}/", 1)[1]
            obj = s3.get_object(Bucket=cfg.s3_bucket, Key=key)
            data = obj["Body"].read()
            fmt = Image.open(io.BytesIO(data)).format
            assert fmt == "PNG", f"page {r['page_num']} MinIO 物件非 PNG（收到 {fmt}）"
            print(f"[gate] page {r['page_num']} patches={r['n_patches']} png={len(data)}B OK")
        print("[gate] PASS — 清理測試資料")
        book_ids = await conn.fetch("SELECT DISTINCT book_id FROM pages WHERE kb_version=$1", KB)
        await conn.execute("DELETE FROM pages WHERE kb_version=$1", KB)  # page_patches cascade
        for b in book_ids:
            await conn.execute("DELETE FROM books WHERE book_id=$1", b["book_id"])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
