"""Orders & payment: the Backend API authenticates the customer and delegates to the
internal Order Service (Backend API -> Order Service, as in the Phase 1 topology)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, status
from fastapi.responses import JSONResponse

from ecommerce_common.cache import CacheService
from ecommerce_common.errors import AppError
from ecommerce_common.models import User

from ..deps import get_cache, get_current_user, get_order_client
from ..schemas import CancelRequest, PaymentConfirm, PaymentCreate, ShippingInfo
from ..services.order_client import OrderServiceClient

orders_router = APIRouter(prefix="/api/orders", tags=["Orders"])
payment_router = APIRouter(prefix="/api/payment", tags=["Payment"])

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9\-_]{8,64}$")


@orders_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Place an order from my cart (reserves stock, status PENDING_PAYMENT)",
    responses={409: {"description": "Insufficient inventory / cart changed"}, 400: {"description": "Cart empty"}},
)
def create_order(
    body: ShippingInfo,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    client: OrderServiceClient = Depends(get_order_client),
    cache: CacheService = Depends(get_cache),
) -> JSONResponse:
    if idempotency_key is not None and not _IDEMPOTENCY_RE.match(idempotency_key):
        raise AppError("Idempotency-Key must be 8-64 characters [A-Za-z0-9-_]", code="INVALID_IDEMPOTENCY_KEY")
    payload = {**body.model_dump(), "idempotency_key": idempotency_key}
    result = client.request(
        "POST", "/internal/orders", user=user, json=payload, retry_safe=idempotency_key is not None
    )
    cache.invalidate_cart(user.id)
    cache.bump_catalog_version()  # available stock changed
    return JSONResponse(result, status_code=status.HTTP_201_CREATED)


@orders_router.get("", summary="My order history")
def list_orders(
    status_filter: str | None = Query(default=None, alias="status", pattern="^[A-Z_]+$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    user: User = Depends(get_current_user),
    client: OrderServiceClient = Depends(get_order_client),
) -> Any:
    return client.request(
        "GET", "/internal/orders", user=user,
        params={"user_id": user.id, "status": status_filter, "page": page, "page_size": page_size},
    )


@orders_router.get("/{order_id}", summary="Order details")
def get_order(
    order_id: int, user: User = Depends(get_current_user), client: OrderServiceClient = Depends(get_order_client)
) -> Any:
    return client.request("GET", f"/internal/orders/{order_id}", user=user, params={"user_id": user.id})


@orders_router.post("/{order_id}/cancel", summary="Cancel my order (only while awaiting payment)")
def cancel_order(
    order_id: int,
    body: CancelRequest = Body(default_factory=CancelRequest),
    user: User = Depends(get_current_user),
    client: OrderServiceClient = Depends(get_order_client),
    cache: CacheService = Depends(get_cache),
) -> Any:
    result = client.request(
        "POST", f"/internal/orders/{order_id}/cancel", user=user,
        json={"reason": body.reason},
    )
    cache.bump_catalog_version()
    return result


@orders_router.get("/{order_id}/invoice", summary="Get a time-limited download URL for the order's PDF invoice")
def get_invoice(
    order_id: int, user: User = Depends(get_current_user), client: OrderServiceClient = Depends(get_order_client)
) -> Any:
    return client.request("GET", f"/internal/orders/{order_id}/invoice", user=user, params={"user_id": user.id})


@payment_router.post("/create", status_code=status.HTTP_201_CREATED, summary="Start a (simulated) payment for an order")
def create_payment(
    body: PaymentCreate, user: User = Depends(get_current_user), client: OrderServiceClient = Depends(get_order_client)
) -> JSONResponse:
    result = client.request(
        "POST", "/internal/payments", user=user, json={"order_id": body.order_id, "user_id": user.id}, retry_safe=True
    )
    return JSONResponse(result, status_code=status.HTTP_201_CREATED)


@payment_router.post(
    "/confirm",
    summary="Confirm the payment with card details (simulator)",
    responses={402: {"description": "Payment declined"}},
)
def confirm_payment(
    body: PaymentConfirm,
    user: User = Depends(get_current_user),
    client: OrderServiceClient = Depends(get_order_client),
    cache: CacheService = Depends(get_cache),
) -> Any:
    try:
        return client.request(
            "POST", "/internal/payments/confirm", user=user,
            json={**body.model_dump(), "user_id": user.id}, retry_safe=True,  # confirm is idempotent
        )
    finally:
        cache.bump_catalog_version()
