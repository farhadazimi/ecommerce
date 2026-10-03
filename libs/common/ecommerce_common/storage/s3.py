"""S3-compatible adapter used for Pardis Cloud OBS (and MinIO locally).

* ``OBS_ENDPOINT``         - endpoint reachable from the pods (VPC endpoint / via NAT)
* ``OBS_PUBLIC_ENDPOINT``  - endpoint browsers use for pre-signed URLs (defaults to OBS_ENDPOINT)
* ``OBS_BUCKET_IMAGES`` / ``OBS_BUCKET_INVOICES`` - per-purpose buckets, fallback ``OBS_BUCKET``
"""

from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..config import Settings
from .base import ObjectNotFoundError, StorageService, StorageUnavailableError, StoredObject, validate_key


class S3StorageService(StorageService):
    name = "s3"

    def __init__(self, settings: Settings) -> None:
        if not settings.obs_bucket and not (settings.obs_bucket_images and settings.obs_bucket_invoices):
            raise ValueError("OBS_BUCKET (or OBS_BUCKET_IMAGES and OBS_BUCKET_INVOICES) must be set")
        self.settings = settings
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.obs_addressing_style},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=3,
            read_timeout=15,
        )
        common = {
            "aws_access_key_id": settings.obs_access_key,
            "aws_secret_access_key": settings.obs_secret_key,
            "region_name": settings.obs_region,
            "config": config,
        }
        self.client = boto3.client("s3", endpoint_url=settings.obs_endpoint, **common)
        public = settings.obs_public_endpoint or settings.obs_endpoint
        self.presign_client = (
            self.client if public == settings.obs_endpoint else boto3.client("s3", endpoint_url=public, **common)
        )

    def bucket_for(self, key: str) -> str:
        if key.startswith("images/"):
            bucket = self.settings.images_bucket
        elif key.startswith("invoices/"):
            bucket = self.settings.invoices_bucket
        else:
            bucket = self.settings.obs_bucket
        if not bucket:
            raise ValueError(f"no bucket configured for key {key!r}")
        return bucket

    def upload(self, key: str, data: bytes, content_type: str) -> StoredObject:
        validate_key(key)
        bucket = self.bucket_for(key)
        extra = {}
        if key.startswith("invoices/"):
            extra["ServerSideEncryption"] = "AES256"  # encryption at rest for invoices
        try:
            self.client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type, **extra)
        except ClientError as exc:
            if extra and exc.response.get("Error", {}).get("Code") in ("NotImplemented", "InvalidArgument"):
                # backend without SSE support (e.g. plain MinIO) - rely on bucket default encryption
                self._put_plain(bucket, key, data, content_type)
            else:
                raise StorageUnavailableError("Object storage is unavailable") from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError("Object storage is unavailable") from exc
        return StoredObject(key=key, bucket=bucket, size=len(data), content_type=content_type)

    def _put_plain(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        try:
            self.client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        except (ClientError, BotoCoreError) as exc:
            raise StorageUnavailableError("Object storage is unavailable") from exc

    def download(self, key: str) -> bytes:
        validate_key(key)
        try:
            obj = self.client.get_object(Bucket=self.bucket_for(key), Key=key)
            return obj["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFoundError("Object not found") from exc
            raise StorageUnavailableError("Object storage is unavailable") from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError("Object storage is unavailable") from exc

    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket_for(key), Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise StorageUnavailableError("Object storage is unavailable") from exc

    def generate_signed_url(self, key: str, expires_in: int | None = None, download_name: str | None = None) -> str:
        validate_key(key)
        params = {"Bucket": self.bucket_for(key), "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self.presign_client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in or self.settings.obs_signed_url_ttl
        )

    def public_url(self, key: str) -> str | None:
        base = self.settings.obs_images_public_base_url
        if base and key.startswith("images/"):
            return f"{base.rstrip('/')}/{key}"
        return None

    def ping(self) -> None:
        buckets = {b for b in (self.settings.images_bucket, self.settings.invoices_bucket) if b}
        try:
            for bucket in buckets:
                self.client.head_bucket(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise StorageUnavailableError("Object storage is unavailable") from exc
