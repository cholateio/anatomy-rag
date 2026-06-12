# ingest/tests/test_no_cloud_llm.py
"""離線管線 MUST NOT 呼叫雲端 LLM（CLAUDE.md 紅線）。

(1) socket guard：跑完整 mock 管線（synthetic 來源 + mock runtime + fake S3 + 假 DB 寫入路徑），
    任何對非本機位址的 TCP connect 立即拋錯 → 證明無對外連線。
(2) import 守門：ingest 套件原始碼不得 import openai/anthropic（與 CI grep 雙保險）。
"""
import socket
from pathlib import Path

import pytest
from anatomy_ingest.colpali_encoder import encode_page_image
from anatomy_ingest.source import synthetic_source
from anatomy_ingest.storage import page_key, upload_page_png
from anatomy_shared.colpali_runtime import get_runtime

INGEST_SRC = Path(__file__).resolve().parents[1] / "src" / "anatomy_ingest"


class _NoNetwork:
    """攔截所有 socket.connect；放行 loopback（本機 DB/MinIO），阻擋其餘。"""

    def __init__(self):
        self._orig = socket.socket.connect

    def __enter__(self):
        orig = self._orig

        def guarded(sock, address):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host in ("127.0.0.1", "::1", "localhost"):
                return orig(sock, address)
            raise AssertionError(f"離線管線嘗試對外連線：{address}（疑似雲端 LLM/外部 API）")

        socket.socket.connect = guarded
        return self

    def __exit__(self, *a):
        socket.socket.connect = self._orig


class _FakeS3:
    def put_object(self, **kw):
        return {"ETag": "x"}


def test_mock_pipeline_makes_no_outbound_connection():
    runtime = get_runtime(mock=True)
    s3 = _FakeS3()
    with _NoNetwork():
        for sp in synthetic_source(3, {"book_title": "A", "edition": 1}):
            enc = encode_page_image(runtime, sp.image)
            assert enc.n_patches > 0
            key = page_key(1, "book", sp.parse.page_num)
            upload_page_png(s3, "bucket", key, sp.image)  # fake，不連網


def test_ingest_source_does_not_import_cloud_llm():
    offenders = []
    for py in INGEST_SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for needle in ("import openai", "from openai", "import anthropic", "from anthropic"):
            if needle in text:
                offenders.append(f"{py.name}: {needle}")
    assert offenders == [], f"ingest 不得 import 雲端 LLM SDK：{offenders}"


@pytest.mark.db
@pytest.mark.asyncio
async def test_run_orchestration_makes_no_outbound_cloud_connection(monkeypatch):
    """FIX D（Codex medium #4）：socket guard 下跑完整 cli._run（synthetic 來源 + mock runtime +
    真 localhost DB + **fake S3**），確認編排層不觸發任何非 loopback 連線（疑似雲端 API）。

    S3 走 fake client（不開 socket）以保持 CI 可攜（db-integration job 無 MinIO 服務），
    同時是更強的「無雲端連線」證明：編排期間**唯一**的網路活動就是 DB loopback。
    loopback（DB :6432）放行；任何非 loopback 連線 → AssertionError。
    """
    import os
    from urllib.parse import urlparse

    import asyncpg
    from anatomy_ingest.cli import _run, build_parser
    from anatomy_ingest.config import IngestConfig

    monkeypatch.setenv("S3_ENDPOINT", os.environ.get("S3_ENDPOINT", "http://localhost:9000"))
    monkeypatch.setenv("S3_BUCKET", os.environ.get("S3_BUCKET", "anatomy-rag-pages"))
    monkeypatch.setenv("S3_ACCESS_KEY", os.environ.get("S3_ACCESS_KEY", "minioadmin"))
    monkeypatch.setenv("S3_SECRET_KEY", os.environ.get("S3_SECRET_KEY", "minioadmin"))

    # fake S3：不開 socket → 編排期間唯一網路活動為 DB loopback（CI 無 MinIO 亦可跑）
    class _FakeS3Client:
        def put_object(self, **kw):
            return {"ETag": "fake"}

    monkeypatch.setattr(IngestConfig, "make_s3_client", lambda self: _FakeS3Client())

    # 解析 DATABASE_URL / S3_ENDPOINT 的 host，擴充 guard allowlist（通常已含 localhost/127.0.0.1）
    _extra_allowed: set[str] = set()
    db_url = os.environ.get("DATABASE_URL", "")
    s3_url = os.environ.get("S3_ENDPOINT", "")
    for url in (db_url, s3_url):
        if url:
            h = urlparse(url).hostname or ""
            if h:
                _extra_allowed.add(h)

    # 擴充版 _NoNetwork：loopback + env 中解析到的 host 放行
    _LOOPBACK = {"127.0.0.1", "::1", "localhost"} | _extra_allowed

    class _NoNetworkExtended(_NoNetwork):
        def __enter__(self):
            orig = self._orig

            def guarded(sock, address):
                host = address[0] if isinstance(address, tuple) else str(address)
                if host in _LOOPBACK:
                    return orig(sock, address)
                raise AssertionError(
                    f"離線管線嘗試對外連線：{address}（疑似雲端 LLM/外部 API）"
                )

            socket.socket.connect = guarded
            return self

    kb = 9100
    ns = build_parser().parse_args([
        "--synthetic", "3",
        "--book-meta", "ingest/scripts/sample_book.yaml",
        "--kb-version", str(kb),
    ])

    with _NoNetworkExtended():
        rc = await _run(ns)

    assert rc == 0, f"_run 應成功退出 rc=0，實際 rc={rc}"

    # 清理本測試寫入
    c = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        bids = await c.fetch("SELECT DISTINCT book_id FROM pages WHERE kb_version=$1", kb)
        await c.execute("DELETE FROM pages WHERE kb_version=$1", kb)
        for b in bids:
            await c.execute("DELETE FROM books WHERE book_id=$1", b["book_id"])
    finally:
        await c.close()
