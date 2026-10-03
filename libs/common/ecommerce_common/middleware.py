"""Pure-ASGI middleware: forwarded headers, request context/access log, security headers.

Order (outermost first): ProxyHeaders -> RequestContext -> SecurityHeaders -> app
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
import uuid
from collections.abc import Callable, Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .log import request_id_var, user_id_var
from .metrics import HTTP_ERRORS, HTTP_IN_FLIGHT, HTTP_LATENCY, HTTP_REQUESTS

access_logger = logging.getLogger("ecommerce.access")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{8,128}$")
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class ProxyHeadersMiddleware:
    """Honour X-Forwarded-For/-Proto/-Host only when the direct peer is a trusted proxy.

    Traffic path in the cloud: WAF -> ELB -> Ingress -> Pod. TLS terminates at the ELB/WAF,
    so ``X-Forwarded-Proto`` tells us whether the original request was HTTPS. The client
    IP is the right-most address in ``X-Forwarded-For`` that is *not* a trusted proxy,
    which prevents clients from spoofing their IP by sending the header themselves.
    """

    def __init__(self, app: ASGIApp, trusted: Sequence[Network]) -> None:
        self.app = app
        self.trusted = list(trusted)
        self.trust_all = any(net.prefixlen == 0 for net in self.trusted)  # TRUSTED_PROXIES="*"

    def _is_trusted(self, host: str | None) -> bool:
        if self.trust_all:
            return True
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(ip in net for net in self.trusted)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        client = scope.get("client")
        if client and self._is_trusted(client[0]):
            scope = dict(scope)
            xff = _header(scope, b"x-forwarded-for")
            if xff:
                hops = [h.strip() for h in xff.split(",") if h.strip()]
                client_ip = hops[0] if hops else client[0]
                for hop in reversed(hops):
                    if not self._is_trusted(hop):
                        client_ip = hop
                        break
                scope["client"] = (client_ip, 0)
            proto = _header(scope, b"x-forwarded-proto")
            if proto:
                proto = proto.split(",")[0].strip().lower()
                if proto in ("http", "https"):
                    scope["scheme"] = proto if scope["type"] == "http" else ("wss" if proto == "https" else "ws")
            fwd_host = _header(scope, b"x-forwarded-host")
            if fwd_host:
                fwd_host = fwd_host.split(",")[0].strip()
                headers = [(k, v) for k, v in scope["headers"] if k != b"host"]
                headers.append((b"host", fwd_host.encode("latin-1")))
                scope["headers"] = headers
        await self.app(scope, receive, send)


class RequestContextMiddleware:
    """Request ID propagation, structured access log and HTTP metrics."""

    def __init__(self, app: ASGIApp, service_name: str, skip_paths: Sequence[str] = ("/metrics",)) -> None:
        self.app = app
        self.service = service_name
        self.skip_paths = set(skip_paths)
        self.quiet_paths = {"/health", "/ready"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        incoming = _header(scope, b"x-request-id")
        request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        rid_token = request_id_var.set(request_id)
        uid_token = user_id_var.set(None)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        start = time.perf_counter()
        status_holder = {"status": 500}
        HTTP_IN_FLIGHT.labels(self.service).inc()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            HTTP_IN_FLIGHT.labels(self.service).dec()
            elapsed = time.perf_counter() - start
            path = scope.get("path", "")
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            status = status_holder["status"]
            method = scope.get("method", "")
            if path not in self.skip_paths:
                HTTP_REQUESTS.labels(self.service, method, route, str(status)).inc()
                HTTP_LATENCY.labels(self.service, method, route).observe(elapsed)
                if status >= 500:
                    HTTP_ERRORS.labels(self.service, route).inc()
                level = logging.DEBUG if path in self.quiet_paths and status < 400 else logging.INFO
                if status >= 500:
                    level = logging.ERROR
                elif status >= 400:
                    level = logging.WARNING
                client = scope.get("client")
                access_logger.log(
                    level,
                    "http_request",
                    extra={
                        "http_method": method,
                        "path": path,
                        "route": route,
                        "status_code": status,
                        "duration_ms": round(elapsed * 1000, 2),
                        "client_ip": client[0] if client else None,
                        "user_agent": (_header(scope, b"user-agent") or "")[:200],
                        "scheme": scope.get("scheme"),
                    },
                )
            request_id_var.reset(rid_token)
            user_id_var.reset(uid_token)


class SecurityHeadersMiddleware:
    """Defensive headers for a JSON API (the SPA's headers are set by its nginx)."""

    def __init__(self, app: ASGIApp, hsts: bool = True, csp_for: Callable[[str], str | None] | None = None) -> None:
        self.app = app
        self.hsts = hsts
        self.csp_for = csp_for or (lambda _path: "default-src 'none'; frame-ancestors 'none'")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        csp = self.csp_for(scope.get("path", ""))
        is_https = scope.get("scheme") == "https"

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ]
                if csp:
                    extra.append((b"content-security-policy", csp.encode()))
                if self.hsts and is_https:
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                headers.extend(h for h in extra if h[0] not in existing)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
