"""Create the configured buckets on an S3-compatible *emulator* (local / CI only).

    python -m ecommerce_common.storage.bootstrap

In Pardis Cloud the OBS buckets (ecommerce-images, ecommerce-invoices) are created by the
platform team with versioning, lifecycle rules and bucket policies; this command
refuses to run in staging/production.
"""

from __future__ import annotations

import sys
import time

from botocore.exceptions import BotoCoreError, ClientError

from ..config import get_settings
from .s3 import S3StorageService


def main() -> None:
    settings = get_settings()
    if settings.is_production_like:
        sys.exit("bucket bootstrap is for local emulators only; create OBS buckets through the cloud console/IaC")
    if settings.storage_backend != "s3":
        print("STORAGE_BACKEND is not s3 - nothing to do")
        return
    storage = S3StorageService(settings)
    buckets = {b for b in (settings.obs_bucket, settings.images_bucket, settings.invoices_bucket) if b}
    for _attempt in range(30):
        try:
            existing = {b["Name"] for b in storage.client.list_buckets().get("Buckets", [])}
            break
        except (BotoCoreError, ClientError) as exc:
            print(f"waiting for object storage ({type(exc).__name__})...")
            time.sleep(2)
    else:
        sys.exit("object storage not reachable")
    for bucket in sorted(buckets - existing):
        storage.client.create_bucket(Bucket=bucket)
        print(f"created bucket {bucket}")
    print(f"buckets ready: {sorted(buckets)}")


if __name__ == "__main__":
    main()
