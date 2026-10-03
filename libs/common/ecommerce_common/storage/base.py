from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..errors import NotFoundError, ServiceUnavailableError

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,500}$")


class StorageUnavailableError(ServiceUnavailableError):
    code = "STORAGE_UNAVAILABLE"


class ObjectNotFoundError(NotFoundError):
    code = "OBJECT_NOT_FOUND"


@dataclass(frozen=True)
class StoredObject:
    key: str
    bucket: str
    size: int
    content_type: str


def validate_key(key: str) -> str:
    if not _KEY_RE.match(key) or ".." in key or "//" in key:
        raise ValueError(f"invalid object key: {key!r}")
    return key


class StorageService(ABC):
    """Contract every storage adapter implements."""

    name = "abstract"

    @abstractmethod
    def upload(self, key: str, data: bytes, content_type: str) -> StoredObject: ...

    @abstractmethod
    def download(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def generate_signed_url(self, key: str, expires_in: int | None = None, download_name: str | None = None) -> str:
        """Return a time-limited URL a browser can use to fetch the object."""

    def public_url(self, key: str) -> str | None:
        """URL for publicly readable objects (product images), if configured."""
        return None

    def url_for(self, key: str, expires_in: int | None = None) -> str:
        return self.public_url(key) or self.generate_signed_url(key, expires_in)

    @abstractmethod
    def ping(self) -> None: ...
