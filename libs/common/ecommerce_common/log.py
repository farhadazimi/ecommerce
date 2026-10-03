"""Structured JSON logging to stdout (collected by CCE -> LTS).

* one JSON object per line
* request_id / user_id pulled from context variables automatically
* keys that look sensitive are masked recursively before serialisation
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
user_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("user_id", default=None)

SENSITIVE_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "card_number",
    "cvv",
    "access_key",
    "api_key",
)
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_service_name = "ecommerce"


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if any(s in str(k).lower() for s in SENSITIVE_KEYS) else mask_sensitive(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [mask_sensitive(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": _service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        uid = user_id_var.get()
        if uid is not None:
            payload["user_id"] = uid
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        payload.update(mask_sensitive(extras))
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["stack"] = "".join(traceback.format_exception(*record.exc_info))[-4000:]
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        rid = request_id_var.get()
        prefix = f"[{rid}] " if rid else ""
        return f"{prefix}{base} {json.dumps(mask_sensitive(extras), default=str) if extras else ''}".rstrip()


def configure_logging(service_name: str, level: str = "INFO", json_logs: bool = True) -> None:
    global _service_name
    _service_name = service_name
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_logs else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn's own access log is replaced by our structured access log middleware
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


event_logger = logging.getLogger("ecommerce.events")


def log_event(event: str, **fields: Any) -> None:
    """Emit a business/audit event (USER_REGISTERED, ORDER_CREATED, ...)."""
    event_logger.info(event, extra={"event": event, **fields})
