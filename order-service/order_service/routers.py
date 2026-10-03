"""Internal API consumed by the Backend API (not routed by the Ingress)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .deps import Caller, get_db, get_order_service, require_admin, require_user
from .schemas import CancelBody, OrderCreate, PaymentConfirmBody, PaymentCreateBody, StatusUpdate
from .services.orders import OrderService, Scope, order_to_dict, payment_to_dict

router = APIRouter(prefix="/internal", tags=["Internal"])


def _scope(caller: Caller) -> Scope:
    return Scope(user_id=caller.user_id, is_admin=caller.is_admin)


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    body: OrderCreate,
    caller: Caller = Depends(require_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> JSONResponse:
    order, created = service.place_order(db, caller.user_id, body)  # type: ignore[arg-type]
    return JSONResponse(order_to_dict(order), status_code=201 if created else 200)


@router.get("/orders")
def list_orders(
    user_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status", pattern="^[A-Z_]+$"),
    q: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    caller: Caller = Depends(require_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> dict[str, Any]:
    return service.list_orders(db, _scope(caller), user_id, status_filter, q, page, page_size)


@router.get("/orders/{order_id}")
def get_order(
    order_id: int,
    caller: Caller = Depends(require_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> dict[str, Any]:
    return order_to_dict(service.get_order(db, order_id, _scope(caller)))


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    body: CancelBody = Body(default_factory=CancelBody),
    caller: Caller = Depends(require_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> dict[str, Any]:
    scope = Scope(user_id=caller.user_id, is_admin=False)  # customer semantics even for admins' own orders
    return order_to_dict(service.cancel_by_customer(db, order_id, scope, body.reason))


@router.put("/orders/{order_id}/status")
def change_status(
    order_id: int,
    body: StatusUpdate,
    caller: Caller = Depends(require_admin),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> dict[str, Any]:
    order = service.change_status(db, order_id, body.status, caller.user_id, body.note, _scope(caller))
    return order_to_dict(order)


@router.get("/orders/{order_id}/invoice")
def get_invoice(
    order_id: int,
    caller: Caller = Depends(require_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> dict[str, Any]:
    order = service.get_order(db, order_id, _scope(caller))
    order = service.invoices.ensure_invoice(db, order)  # generates on demand if a previous attempt failed
    return {
        "order_id": order.id,
        "invoice_number": order.invoice_number,
        "url": service.invoices.download_url(order),
        "expires_in": 600,
    }


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def create_payment(
    body: PaymentCreateBody,
    caller: Caller = Depends(require_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> dict[str, Any]:
    payment = service.create_payment(db, body.order_id, Scope(user_id=caller.user_id, is_admin=False))
    return {
        **payment_to_dict(payment),
        "test_cards": {
            "success": "4242 4242 4242 4242",
            "declined": "4000 0000 0000 0002",
            "insufficient_funds": "4000 0000 0000 9995",
        },
    }


@router.post("/payments/confirm")
def confirm_payment(
    body: PaymentConfirmBody,
    caller: Caller = Depends(require_user),
    db: Session = Depends(get_db),
    service: OrderService = Depends(get_order_service),
) -> dict[str, Any]:
    order, payment = service.confirm_payment(db, body, Scope(user_id=caller.user_id, is_admin=False))
    return {"payment": payment_to_dict(payment), "order": order_to_dict(order)}
