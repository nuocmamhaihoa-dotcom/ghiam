"""S3 / MinIO client."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config

from app.core.config import get_settings


class S3Client:
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_bucket
        self._expire = settings.s3_presign_expire_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            self._client.create_bucket(Bucket=self._bucket)

    def presign_put(self, key: str, content_type: str) -> dict[str, Any]:
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=self._expire,
        )
        return {
            "method": "PUT",
            "url": url,
            "headers": {"Content-Type": content_type},
            "expires_in": self._expire,
            "key": key,
        }

    def presign_get(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._expire,
        )

    def head_ok(self) -> bool:
        try:
            self._client.list_buckets()
            return True
        except Exception:
            return False


@lru_cache
def get_s3_client() -> S3Client:
    return S3Client()
