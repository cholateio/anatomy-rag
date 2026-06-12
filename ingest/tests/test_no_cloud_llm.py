# ingest/tests/test_no_cloud_llm.py
"""離線管線 MUST NOT 呼叫雲端 LLM（CLAUDE.md 紅線）。

(1) socket guard：跑完整 mock 管線（synthetic 來源 + mock runtime + fake S3 + 假 DB 寫入路徑），
    任何對非本機位址的 TCP connect 立即拋錯 → 證明無對外連線。
(2) import 守門：ingest 套件原始碼不得 import openai/anthropic（與 CI grep 雙保險）。
"""
import socket
from pathlib import Path

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
