"""Object storage abstraction (OBS in the cloud, local filesystem for development).

Business code only deals with *object keys* such as ``images/products/12/ab.jpg`` or
``invoices/2026/09/INV-....pdf``. The adapter maps the key prefix to the right bucket
(``OBS_BUCKET_IMAGES`` / ``OBS_BUCKET_INVOICES`` / ``OBS_BUCKET``).
"""

from __future__ import annotations

from ..config import Settings
from .base import ObjectNotFoundError, StorageService, StorageUnavailableError, StoredObject
from .local import LocalStorageService
from .s3 import S3StorageService

__all__ = [
    "ObjectNotFoundError",
    "StorageService",
    "StorageUnavailableError",
    "StoredObject",
    "LocalStorageService",
    "S3StorageService",
    "build_storage",
    "product_image_key",
    "avatar_key",
    "invoice_key",
]


def build_storage(settings: Settings) -> StorageService:
    if settings.storage_backend == "s3":
        return S3StorageService(settings)
    return LocalStorageService(settings)


def product_image_key(product_id: int, filename: str) -> str:
    return f"images/products/{product_id}/{filename}"


def avatar_key(user_id: int, filename: str) -> str:
    return f"images/avatars/{user_id}/{filename}"


def invoice_key(year: int, month: int, invoice_number: str) -> str:
    return f"invoices/{year:04d}/{month:02d}/{invoice_number}.pdf"
