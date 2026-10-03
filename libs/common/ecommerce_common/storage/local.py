"""Filesystem adapter for local development only.

Objects are served back through the backend's ``/api/files/{key}`` endpoint using an
HMAC-signed, expiring URL so that the API surface matches the OBS adapter. It is
refused in staging/production because container filesystems are ephemeral.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from ..config import Settings
from .base import ObjectNotFoundError, StorageService, StorageUnavailableError, StoredObject, validate_key


class LocalStorageService(StorageService):
    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.local_storage_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.secret = settings.jwt_secret.encode()
        self.base_url = settings.public_base_url.rstrip("/")
        self.default_ttl = settings.obs_signed_url_ttl

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("object key escapes storage root")
        return path

    def upload(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError as exc:
            raise StorageUnavailableError("Object storage is unavailable") from exc
        return StoredObject(key=key, bucket="local", size=len(data), content_type=content_type)

    def download(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError("Object not found")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def sign(self, key: str, expires: int) -> str:
        return hmac.new(self.secret, f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()

    def verify(self, key: str, expires: int, signature: str) -> bool:
        return expires >= int(time.time()) and hmac.compare_digest(self.sign(key, expires), signature)

    def generate_signed_url(self, key: str, expires_in: int | None = None, download_name: str | None = None) -> str:
        validate_key(key)
        expires = int(time.time()) + (expires_in or self.default_ttl)
        # round up to the next minute so URLs are stable (cacheable) within that window
        expires = expires - expires % 60 + 60
        return f"{self.base_url}/api/files/{quote(key)}?" + urlencode(
            {"expires": expires, "signature": self.sign(key, expires)}
        )

    _TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".pdf": "application/pdf"}

    @classmethod
    def guess_content_type(cls, key: str) -> str:
        # explicit map: slim container images often lack /etc/mime.types entries (e.g. webp)
        suffix = Path(key).suffix.lower()
        return cls._TYPES.get(suffix) or mimetypes.guess_type(key)[0] or "application/octet-stream"

    def ping(self) -> None:
        if not self.root.is_dir():
            raise StorageUnavailableError("storage root missing")
