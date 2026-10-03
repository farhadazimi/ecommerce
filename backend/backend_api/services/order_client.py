"""HTTP client for the internal Order Service (Kubernetes DNS: ``http://order-service:8001``).

* service-to-service auth with a shared secret (``X-Internal-Token``) + NetworkPolicy
* caller identity and request id are propagated
* explicit timeouts; retries only for safe/idempotent calls
* errors from the order service are re-raised with the same status and error code
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ecommerce_common.config import Settings
from ecommerce_common.errors import AppError, ServiceUnavailableError
from ecommerce_common.log import request_id_var
from ecommerce_common.models import User

logger = logging.getLogger("ecommerce.order_client")


class OrderServiceClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(
            base_url=settings.order_service_url.rstrip("/"),
            timeout=httpx.Timeout(settings.order_service_timeout, connect=3.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )

    def close(self) -> None:
        self.client.close()

    def _headers(self, user: User | None, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"X-Internal-Token": self.settings.internal_service_token}
        if user is not None:
            headers["X-User-Id"] = str(user.id)
            headers["X-User-Role"] = user.role_name
        rid = request_id_var.get()
        if rid:
            headers["X-Request-ID"] = rid
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        user: User | None,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retry_safe: bool | None = None,
    ) -> Any:
        """Call the order service. ``retry_safe`` defaults to True for GET requests."""
        attempts = 3 if (retry_safe if retry_safe is not None else method == "GET") else 1
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = self.client.request(
                    method, path, json=json, params=params, headers=self._headers(user, headers)
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                logger.warning(
                    "order service call failed",
                    extra={"path": path, "attempt": attempt, "error_type": type(exc).__name__},
                )
                if attempt < attempts:
                    time.sleep(0.2 * attempt)
                continue
            if resp.status_code >= 500 and resp.status_code != 503 and attempt < attempts:
                time.sleep(0.2 * attempt)
                continue
            return self._handle(resp)
        raise ServiceUnavailableError(
            "The order service is temporarily unavailable, please retry", code="ORDER_SERVICE_UNAVAILABLE"
        ) from last_exc

    @staticmethod
    def _handle(resp: httpx.Response) -> Any:
        if resp.status_code < 400:
            return resp.json() if resp.content else None
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        raise AppError(
            err.get("message") or "Order service error",
            code=err.get("code") or "ORDER_SERVICE_ERROR",
            status_code=resp.status_code,
            details=err.get("details"),
        )

    def ping(self) -> None:
        resp = self.client.get("/health", timeout=2.0)
        resp.raise_for_status()
