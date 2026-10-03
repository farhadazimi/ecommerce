"""Image upload validation and optimisation.

Uploaded bytes are never trusted: the image is decoded with Pillow, format-checked,
downscaled and re-encoded as WebP. That strips metadata (EXIF/GPS), neutralises
polyglot files and keeps OBS objects small.
"""

from __future__ import annotations

import io
import uuid

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from ecommerce_common.errors import AppError

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppError(
            f"File is too large (max {max_bytes // (1024 * 1024)} MB)", code="PAYLOAD_TOO_LARGE", status_code=413
        )
    if not data:
        raise AppError("Empty file", code="INVALID_IMAGE")
    return data


def process_image(data: bytes, max_side: int) -> tuple[bytes, str, str]:
    """Return (bytes, content_type, filename)."""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ALLOWED_FORMATS:
            raise AppError("Only JPEG, PNG, WebP or GIF images are accepted", code="UNSUPPORTED_MEDIA_TYPE", status_code=415)
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            img.thumbnail((max_side, max_side))
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=85, method=4)
    except AppError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise AppError("The uploaded file is not a valid image", code="INVALID_IMAGE") from exc
    return out.getvalue(), "image/webp", f"{uuid.uuid4().hex}.webp"
