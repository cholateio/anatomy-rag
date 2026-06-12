"""物件儲存：頁面 PNG 上傳 MinIO/S3（§2.1）。boto3 client 由 config 建立並注入。"""
from __future__ import annotations

import io


def page_key(kb_version: int, book_id: str, page_num: int) -> str:
    """物件鍵：kb_v{N}/{book_id}/page_{num:04d}.png（依版本+書分層，利於刪除/備份）。"""
    return f"kb_v{kb_version}/{book_id}/page_{page_num:04d}.png"


def upload_page_png(s3_client, bucket: str, key: str, image) -> str:
    """把 PIL.Image 編碼為 PNG 上傳，回傳 s3:// URI（寫入 pages.page_image_uri）。"""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    s3_client.put_object(Bucket=bucket, Key=key, Body=buf, ContentType="image/png")
    return f"s3://{bucket}/{key}"
