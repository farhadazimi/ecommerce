"""Serves objects of the *local development* storage adapter via signed URLs.

With the OBS adapter, browsers download directly from OBS using pre-signed URLs and
this endpoint returns 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from ecommerce_common.errors import NotFoundError, PermissionDeniedError
from ecommerce_common.storage import LocalStorageService, StorageService

from ..deps import get_storage

router = APIRouter(prefix="/api/files", tags=["Files"], include_in_schema=False)


@router.get("/{key:path}")
def get_file(
    key: str,
    expires: int = Query(...),
    signature: str = Query(..., min_length=64, max_length=64),
    storage: StorageService = Depends(get_storage),
) -> Response:
    if not isinstance(storage, LocalStorageService):
        raise NotFoundError("Not found")
    try:
        valid = storage.verify(key, expires, signature)
    except ValueError:
        valid = False
    if not valid:
        raise PermissionDeniedError("Link is invalid or has expired", code="INVALID_SIGNATURE")
    data = storage.download(key)
    headers = {"Cache-Control": "private, max-age=300"}
    if key.startswith("invoices/"):
        headers["Content-Disposition"] = f'attachment; filename="{key.rsplit("/", 1)[-1]}"'
    return Response(content=data, media_type=storage.guess_content_type(key), headers=headers)
