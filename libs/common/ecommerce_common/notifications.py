"""Notification adapter (SMN).

``log``     - write the notification as a structured log line (default, dev)
``webhook`` - POST JSON to an HTTP(S) endpoint such as an SMN HTTP topic publisher
              or a small relay function. Delivery is best-effort and asynchronous, so
              a notification outage never breaks checkout.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger("ecommerce.notifications")


class Topics:
    BUSINESS = "business"  # order confirmations, customer notifications
    OPERATIONS = "operations"
    ALERTS = "alerts"
    SECURITY = "security"


class NotificationService:
    def __init__(self, settings: Settings) -> None:
        self.backend = settings.notification_backend
        self.url = settings.smn_webhook_url
        self.token = settings.smn_webhook_token
        self.service = settings.service_name
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify") if self.backend == "webhook" else None

    def publish(self, topic: str, subject: str, message: dict[str, Any]) -> None:
        if self.backend == "webhook" and self.url and self._pool:
            self._pool.submit(self._post, topic, subject, message)
        else:
            logger.info("notification", extra={"topic": topic, "subject": subject, "notification": message})

    def _post(self, topic: str, subject: str, message: dict[str, Any]) -> None:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = httpx.post(
                self.url,  # type: ignore[arg-type]
                json={"topic": topic, "subject": subject, "message": message, "source": self.service},
                headers=headers,
                timeout=3.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("notification delivery failed", extra={"topic": topic, "error_type": type(exc).__name__})

    def close(self) -> None:
        if self._pool:
            self._pool.shutdown(wait=True, cancel_futures=False)
