"""ingest 設定：讀同一組 .env（DB / S3）。不引 pydantic-settings（不新增依賴），純 os.environ。

DB 連 PgBouncer :6432（CLAUDE.md 紅線；migrations 才用 PG_DIRECT_URL，ingest 不碰）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class IngestConfig:
    database_url: str
    s3_bucket: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str

    @classmethod
    def from_env(cls) -> IngestConfig:
        def req(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                raise ValueError(f"缺必要環境變數 {name}")
            return v

        database_url = req("DATABASE_URL")
        port = urlparse(database_url).port
        if port != 6432:
            # 只接受 :6432（無 port / 其他 port 一律拒）
            raise ValueError(
                f"ingest MUST 連 PgBouncer :6432（目前 port={port}）；禁止直連 :5432 或其他 port"
                "（僅 Alembic migrations 用 PG_DIRECT_URL 直連 :5432）"
            )
        return cls(
            database_url=database_url,
            s3_bucket=req("S3_BUCKET"),
            s3_endpoint=req("S3_ENDPOINT"),
            s3_access_key=req("S3_ACCESS_KEY"),
            s3_secret_key=req("S3_SECRET_KEY"),
        )

    def make_s3_client(self):
        """建立 boto3 S3 client（指向 MinIO/S3 endpoint）。"""
        import boto3  # lazy

        return boto3.client(
            "s3",
            endpoint_url=self.s3_endpoint,
            aws_access_key_id=self.s3_access_key,
            aws_secret_access_key=self.s3_secret_key,
        )
