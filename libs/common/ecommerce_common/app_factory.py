"""Common FastAPI wiring shared by the Backend API and the Order Service."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .errors import register_exception_handlers
from .metrics import DEPENDENCY_UP, render_metrics
from .middleware import ProxyHeadersMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger("ecommerce.health")


@dataclass
class DependencyCheck:
    name: str
    check: Callable[[], None]
    critical: bool = True


def install_common(app: FastAPI, settings: Settings, checks: Callable[[], list[DependencyCheck]]) -> None:
    """Attach middleware, error handlers and the /health, /ready, /metrics endpoints."""
    started = time.time()
    app.state.draining = False
    register_exception_handlers(app)

    # add_middleware prepends, so the last one added is the outermost
    app.add_middleware(SecurityHeadersMiddleware, csp_for=_csp_for)
    app.add_middleware(RequestContextMiddleware, service_name=settings.service_name)
    app.add_middleware(ProxyHeadersMiddleware, trusted=settings.trusted_proxy_networks)

    @app.get("/health", tags=["Health"], summary="Liveness: the process is running")
    def health() -> dict:
        return {
            "status": "ok",
            "service": settings.service_name,
            "version": settings.app_version,
            "environment": settings.app_env,
            "uptime_seconds": int(time.time() - started),
        }

    @app.get("/ready", tags=["Health"], summary="Readiness: critical dependencies are reachable")
    async def ready() -> JSONResponse:
        if app.state.draining:
            return JSONResponse({"status": "draining"}, status_code=503)
        results: dict[str, dict] = {}
        ok = True
        for dep in checks():
            t0 = time.perf_counter()
            try:
                await run_in_threadpool(dep.check)
                results[dep.name] = {"status": "ok", "critical": dep.critical}
                DEPENDENCY_UP.labels(dep.name).set(1)
            except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
                results[dep.name] = {"status": "error", "critical": dep.critical, "error": type(exc).__name__}
                DEPENDENCY_UP.labels(dep.name).set(0)
                if dep.critical:
                    ok = False
                logger.warning("readiness check failed", extra={"dependency": dep.name, "error_type": type(exc).__name__})
            results[dep.name]["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        body = {"status": "ready" if ok else "not_ready", "service": settings.service_name, "checks": results}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/metrics", tags=["Health"], summary="Prometheus metrics", include_in_schema=False)
    def metrics() -> Response:
        on_scrape = getattr(app.state, "on_metrics_scrape", None)
        if on_scrape:
            try:
                on_scrape()
            except Exception:  # noqa: BLE001
                logger.debug("metrics scrape hook failed", exc_info=True)
        data, content_type = render_metrics()
        return Response(content=data, media_type=content_type)


def _csp_for(path: str) -> str:
    if path.startswith("/api/docs") or path.startswith("/api/redoc"):
        # Swagger UI loads its assets from jsDelivr
        return (
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
            "frame-ancestors 'none'"
        )
    return "default-src 'none'; frame-ancestors 'none'"
