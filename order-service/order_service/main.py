"""Order Service application factory.

Run: ``uvicorn order_service.main:create_app --factory --host 0.0.0.0 --port 8001``
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ecommerce_common.app_factory import DependencyCheck, install_common
from ecommerce_common.cache import CacheService, build_cache
from ecommerce_common.config import Settings, get_settings
from ecommerce_common.db import Database
from ecommerce_common.log import configure_logging
from ecommerce_common.notifications import NotificationService
from ecommerce_common.storage import StorageService, build_storage

from .routers import router
from .services.invoices import InvoiceService
from .services.orders import OrderService
from .services.payment_gateway import PaymentGateway, build_gateway
from .services.reaper import OrderReaper

logger = logging.getLogger("ecommerce.order_service")


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    cache: CacheService | None = None,
    storage: StorageService | None = None,
    gateway: PaymentGateway | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if settings.service_name == "ecommerce":
        settings = settings.model_copy(update={"service_name": "order-service"})
    configure_logging(settings.service_name, settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("starting", extra={"environment": settings.app_env, "storage_backend": app.state.storage.name,
                                       "cache_backend": app.state.cache.backend_name})
        if settings.order_reaper_enabled:
            app.state.reaper.start()
        yield
        app.state.draining = True
        logger.info("shutting down")
        app.state.reaper.stop()
        app.state.notifier.close()
        app.state.db.dispose()

    app = FastAPI(
        title="E-Commerce Order Service (internal)",
        version=settings.app_version,
        description="Internal order processing API. Only reachable from the Backend API inside the cluster.",
        docs_url="/docs" if settings.expose_api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.expose_api_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database or Database(settings)
    app.state.cache = cache or build_cache(settings)
    app.state.storage = storage or build_storage(settings)
    app.state.notifier = NotificationService(settings)
    invoices = InvoiceService(app.state.storage, settings)
    app.state.order_service = OrderService(settings, app.state.cache, gateway or build_gateway(), invoices, app.state.notifier)
    app.state.reaper = OrderReaper(
        app.state.db, app.state.order_service, settings.order_payment_timeout_minutes, settings.order_reaper_interval_seconds
    )

    def checks() -> list[DependencyCheck]:
        return [
            DependencyCheck("database", app.state.db.ping, critical=True),
            DependencyCheck("redis", app.state.cache.ping, critical=settings.redis_required),
        ]

    install_common(app, settings, checks)
    app.include_router(router)
    return app
