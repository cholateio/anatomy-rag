# ingest/tests/test_storage.py
import io

from anatomy_ingest.storage import page_key, upload_page_png
from PIL import Image


def test_page_key_scheme():
    k = page_key(kb_version=2, book_id="b1234", page_num=7)
    assert k == "kb_v2/b1234/page_0007.png"


class _FakeS3:
    def __init__(self):
        self.puts = []
    def put_object(self, **kw):
        self.puts.append(kw)
        return {"ETag": "x"}


def test_upload_page_png_puts_png_bytes_and_returns_uri():
    s3 = _FakeS3()
    img = Image.new("RGB", (10, 10), "white")
    uri = upload_page_png(s3, bucket="anatomy-rag-pages", key="kb_v1/b/page_0001.png", image=img)
    assert uri == "s3://anatomy-rag-pages/kb_v1/b/page_0001.png"
    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Bucket"] == "anatomy-rag-pages" and put["Key"] == "kb_v1/b/page_0001.png"
    assert put["ContentType"] == "image/png"
    # body 為合法 PNG
    body = put["Body"]
    data = body.getvalue() if hasattr(body, "getvalue") else body
    assert Image.open(io.BytesIO(data)).format == "PNG"
