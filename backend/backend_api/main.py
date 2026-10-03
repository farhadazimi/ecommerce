"""Backend API application factory.

Run: ``uvicorn backend_api.main:create_app --factory --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ecommerce_common.app_factory import DependencyCheck, install_common
from ecommerce_common.cache import CacheService, build_cache
from ecommerce_common.config import Settings, get_settings
from ecommerce_common.db import Database
from ecommerce_common.log import configure_logging
from ecommerce_common.metrics import ACTIVE_USERS
from ecommerce_common.notifications import NotificationService
from ecommerce_common.storage import StorageService, build_storage

from .deps import default_rate_limit
from .routers import admin, auth, cart, categories, files, orders, products, profile
from .schemas import ErrorResponse
from .services.order_client import OrderServiceClient

logger = logging.getLogger("ecommerce.backend")

OPENAPI_TAGS = [
    {"name": "Authentication", "description": "Register, login/logout (JWT in body + HttpOnly cookie)"},
    {"name": "Profile", "description": "Customer profile and avatar (OBS images/avatars/)"},
    {"name": "Products", "description": "Catalog browsing, search and admin product management"},
    {"name": "Categories", "description": "Product categories"},
    {"name": "Cart", "description": "Shopping cart (RDS + Redis cache)"},
    {"name": "Orders", "description": "Order placement and history (delegated to the Order Service)"},
    {"name": "Payment", "description": "Payment simulation (replaceable gateway)"},
    {"name": "Admin", "description": "Administration: users, orders, inventory, statistics"},
    {"name": "Health", "description": "Liveness / readiness probes and metrics"},
]


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    cache: CacheService | None = None,
    storage: StorageService | None = None,
    order_client: OrderServiceClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if settings.service_name == "ecommerce":
        settings = settings.model_copy(update={"service_name": "backend-api"})
    configure_logging(settings.service_name, settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "starting",
            extra={
                "environment": settings.app_env,
                "storage_backend": app.state.storage.name,
                "cache_backend": app.state.cache.backend_name,
                "order_service_url": settings.order_service_url,
            },
        )
        yield
        app.state.draining = True
        logger.info("shutting down")
        app.state.order_client.close()
        app.state.notifier.close()
        app.state.db.dispose()

    docs = settings.expose_api_docs
    app = FastAPI(
        title="E-Commerce Backend API",
        version=settings.app_version,
        description=(
            "Public REST API of the Plan B e-commerce platform (Pardis Cloud Phase 1). "
            "All errors use the envelope `{\"error\": {\"code\", \"message\", \"details\", \"request_id\"}}`."
        ),
        openapi_tags=OPENAPI_TAGS,
        docs_url="/api/docs" if docs else None,
        redoc_url="/api/redoc" if docs else None,
        openapi_url="/api/openapi.json" if docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database or Database(settings)
    app.state.cache = cache or build_cache(settings)
    app.state.storage = storage or build_storage(settings)
    app.state.notifier = NotificationService(settings)
    app.state.order_client = order_client or OrderServiceClient(settings)
    app.state.on_metrics_scrape = lambda: ACTIVE_USERS.set(app.state.cache.active_user_count())

    def checks() -> list[DependencyCheck]:
        return [
            DependencyCheck("database", app.state.db.ping, critical=True),
            DependencyCheck("redis", app.state.cache.ping, critical=settings.redis_required),
        ]

    install_common(app, settings, checks)
    # CORS is added last so it wraps everything (including error responses)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    @app.get("/api/health", tags=["Health"], summary="Liveness via the ingress path (for ELB / Cloud Eye probes)")
    def api_health() -> dict:
        return {"status": "ok", "service": settings.service_name, "version": settings.app_version}

    limited = [Depends(default_rate_limit)]
    error_responses = {
        code: {"model": ErrorResponse, "description": desc}
        for code, desc in {
            400: "Bad request", 401: "Not authenticated / token expired", 403: "Forbidden (role)", 404: "Not found",
            409: "Conflict (e.g. insufficient inventory)", 422: "Validation error", 429: "Rate limited",
            503: "Dependency unavailable (database, order service, object storage)",
        }.items()
    }
    for module_router in (
        auth.router,
        profile.router,
        products.router,
        categories.router,
        cart.router,
        orders.orders_router,
        orders.payment_router,
        admin.router,
    ):
        app.include_router(module_router, dependencies=limited, responses=error_responses)
    app.include_router(files.router)
    return app

