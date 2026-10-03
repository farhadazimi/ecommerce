"""Order lifecycle.

::

    cart ──place_order──▶ PENDING_PAYMENT (stock reserved)
                               │  payment SUCCEEDED ─▶ PAID (stock decremented, invoice → OBS)
                               │  payment FAILED    ─▶ stays PENDING_PAYMENT (customer may retry)
                               │  timeout / cancel  ─▶ CANCELLED (reservation released)
    PAID ─▶ PROCESSING ─▶ SHIPPED ─▶ DELIVERED
    PAID / PROCESSING ─▶ CANCELLED (stock returned, payment REFUNDED)

Concurrency: inventory rows are locked with ``SELECT … FOR UPDATE`` in primary-key
order, so concurrent checkouts on different pods can never oversell or deadlock.
"""

from __future__ import annotations

import logging
import math
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ecommerce_common.cache import CacheService
from ecommerce_common.config import Settings
from ecommerce_common.errors import AppError, ConflictError, NotFoundError
from ecommerce_common.log import log_event
from ecommerce_common.metrics import ORDER_STATUS_CHANGES, ORDERS_CREATED, PAYMENT_FAILURES, PAYMENTS
from ecommerce_common.models import (
    Cart,
    Inventory,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Payment,
    PaymentStatus,
    User,
    utcnow,
)
from ecommerce_common.notifications import NotificationService, Topics
from ecommerce_common.pricing import money, shipping_fee
from ecommerce_common.storage import StorageUnavailableError

from ..schemas import OrderCreate, PaymentConfirmBody
from .invoices import InvoiceService
from .payment_gateway import CardDetails, PaymentGateway

logger = logging.getLogger("ecommerce.orders")

TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PENDING_PAYMENT: {OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class Scope:
    """Who is asking: customers only see their own orders; admins see all."""

    user_id: int | None
    is_admin: bool


def _money(v: Decimal) -> float:
    return float(v)


def order_to_dict(order: Order, detailed: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": order.id,
        "order_number": order.order_number,
        "status": order.status,
        "currency": order.currency,
        "subtotal": _money(order.subtotal),
        "shipping_fee": _money(order.shipping_fee),
        "total": _money(order.total),
        "item_count": sum(i.quantity for i in order.items),
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "customer": {"id": order.user.id, "email": order.user.email, "full_name": order.user.full_name},
        "invoice": {"number": order.invoice_number, "available": bool(order.invoice_key)},
        "items": [
            {
                "id": i.id, "product_id": i.product_id, "sku": i.sku, "product_name": i.product_name,
                "unit_price": _money(i.unit_price), "quantity": i.quantity, "line_total": _money(i.line_total),
            }
            for i in order.items
        ],
    }
    if detailed:
        data["shipping"] = {
            "name": order.shipping_name, "phone": order.shipping_phone,
            "address_line1": order.shipping_address_line1, "address_line2": order.shipping_address_line2,
            "city": order.shipping_city, "postal_code": order.shipping_postal_code, "country": order.shipping_country,
        }
        data["notes"] = order.notes
        data["payments"] = [payment_to_dict(p) for p in order.payments]
        data["history"] = [
            {"from_status": h.from_status, "to_status": h.to_status, "note": h.note, "changed_by": h.changed_by,
             "created_at": h.created_at.isoformat()}
            for h in order.history
        ]
    return data


def payment_to_dict(p: Payment) -> dict[str, Any]:
    return {
        "id": p.id, "payment_ref": p.payment_ref, "order_id": p.order_id, "provider": p.provider,
        "amount": _money(p.amount), "currency": p.currency, "status": p.status, "card_brand": p.card_brand,
        "card_last4": p.card_last4, "failure_code": p.failure_code, "failure_reason": p.failure_reason,
        "created_at": p.created_at.isoformat(), "confirmed_at": p.confirmed_at.isoformat() if p.confirmed_at else None,
    }


class OrderService:
    def __init__(
        self,
        settings: Settings,
        cache: CacheService,
        gateway: PaymentGateway,
        invoices: InvoiceService,
        notifier: NotificationService,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.gateway = gateway
        self.invoices = invoices
        self.notifier = notifier

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _lock_inventory(db: Session, product_ids: list[int]) -> dict[int, Inventory]:
        rows = db.scalars(
            select(Inventory)
            .where(Inventory.product_id.in_(sorted(set(product_ids))))
            .order_by(Inventory.product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        return {inv.product_id: inv for inv in rows}

    @staticmethod
    def _lock_order(db: Session, order_id: int) -> Order | None:
        return db.scalar(
            select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
        )

    def get_order(self, db: Session, order_id: int, scope: Scope, lock: bool = False) -> Order:
        order = self._lock_order(db, order_id) if lock else db.get(Order, order_id)
        if order is None or (not scope.is_admin and order.user_id != scope.user_id):
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        return order

    @staticmethod
    def _record(order: Order, to_status: str, changed_by: int | None, note: str | None = None) -> None:
        from_status = order.status if order.id else None
        order.history.append(
            OrderStatusHistory(from_status=from_status, to_status=to_status, changed_by=changed_by, note=note)
        )
        order.status = to_status
        ORDER_STATUS_CHANGES.labels(to_status).inc()

    # ------------------------------------------------------------------ place order
    def place_order(self, db: Session, user_id: int, data: OrderCreate) -> tuple[Order, bool]:
        """Create an order from the user's cart. Returns (order, created)."""
        key = data.idempotency_key
        if key:
            existing = db.scalar(select(Order).where(Order.user_id == user_id, Order.idempotency_key == key))
            if existing:
                return existing, False

        cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
        if cart is None or not cart.items:
            raise AppError("Your cart is empty", code="CART_EMPTY")

        inventories = self._lock_inventory(db, [i.product_id for i in cart.items])
        problems = []
        for item in cart.items:
            p, inv = item.product, inventories.get(item.product_id)
            if not p.is_active or p.deleted_at is not None:
                problems.append({"product_id": p.id, "name": p.name, "reason": "UNAVAILABLE"})
            elif inv is None or inv.available < item.quantity:
                problems.append({
                    "product_id": p.id, "name": p.name, "reason": "INSUFFICIENT_STOCK",
                    "requested": item.quantity, "available": inv.available if inv else 0,
                })
        if problems:
            db.rollback()
            raise ConflictError(
                "Some items are not available in the requested quantity", code="INSUFFICIENT_INVENTORY", details=problems
            )

        order = Order(
            order_number=f"ORD-{utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}",
            user_id=user_id,
            status=OrderStatus.PENDING_PAYMENT,
            currency=self.settings.currency,
            idempotency_key=key,
            **data.model_dump(exclude={"idempotency_key"}),
        )
        subtotal = Decimal("0")
        for item in cart.items:
            p = item.product
            line_total = money(p.price * item.quantity)
            subtotal += line_total
            order.items.append(OrderItem(
                product_id=p.id, sku=p.sku, product_name=p.name, unit_price=p.price, quantity=item.quantity,
                line_total=line_total,
            ))
            inventories[p.id].reserved += item.quantity
        order.subtotal = money(subtotal)
        order.shipping_fee = shipping_fee(order.subtotal, self.settings)
        order.total = money(order.subtotal + order.shipping_fee)
        order.history.append(OrderStatusHistory(from_status=None, to_status=OrderStatus.PENDING_PAYMENT,
                                                changed_by=user_id, note="Order placed"))
        cart.items.clear()
        db.add(order)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if key:  # the same request raced on another pod - return the winner
                existing = db.scalar(select(Order).where(Order.user_id == user_id, Order.idempotency_key == key))
                if existing:
                    return existing, False
            raise
        db.refresh(order)
        self.cache.invalidate_cart(user_id)
        self.cache.bump_catalog_version()
        ORDERS_CREATED.inc()
        log_event("ORDER_CREATED", user_id=user_id, order_id=order.id, order_number=order.order_number,
                  total=str(order.total), items=len(order.items))
        return order, True

    # ------------------------------------------------------------------ queries
    def list_orders(
        self, db: Session, scope: Scope, user_id: int | None, status: str | None, q: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        query = select(Order)
        effective_user = user_id if scope.is_admin else scope.user_id
        if effective_user is not None:
            query = query.where(Order.user_id == effective_user)
        if status:
            query = query.where(Order.status == status)
        if q and scope.is_admin:
            pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.join(User, User.id == Order.user_id).where(
                or_(Order.order_number.like(pattern, escape="\\"), User.email.like(pattern, escape="\\"))
            )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        orders = db.scalars(
            query.order_by(Order.created_at.desc(), Order.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).unique().all()
        return {
            "items": [order_to_dict(o, detailed=False) for o in orders],
            "total": total, "page": page, "page_size": page_size,
            "pages": math.ceil(total / page_size) if total else 0,
        }

    # ------------------------------------------------------------------ cancellation / status
    def _release_or_restock(self, db: Session, order: Order) -> None:
        inventories = self._lock_inventory(db, [i.product_id for i in order.items if i.product_id])
        for item in order.items:
            inv = inventories.get(item.product_id) if item.product_id else None
            if inv is None:
                continue
            if order.status == OrderStatus.PENDING_PAYMENT:
                inv.reserved = max(inv.reserved - item.quantity, 0)  # release reservation
            else:
                inv.quantity += item.quantity  # goods return to stock
        for p in order.payments:
            if p.status == PaymentStatus.PENDING:
                p.status, p.failure_code, p.failure_reason = PaymentStatus.FAILED, "ORDER_CANCELLED", "Order cancelled"
            elif p.status == PaymentStatus.SUCCEEDED:
                p.status = PaymentStatus.REFUNDED  # simulated refund

    def change_status(self, db: Session, order_id: int, new_status: str, actor: int | None, note: str | None,
                      scope: Scope) -> Order:
        order = self.get_order(db, order_id, scope, lock=True)
        old = order.status
        if new_status == old:
            return order
        if new_status not in TRANSITIONS.get(old, set()):
            db.rollback()
            raise ConflictError(f"Cannot change order status from {old} to {new_status}", code="INVALID_STATUS_TRANSITION",
                                details={"from": old, "to": new_status, "allowed": sorted(TRANSITIONS.get(old, set()))})
        if new_status == OrderStatus.CANCELLED:
            self._release_or_restock(db, order)
            order.cancelled_at = utcnow()
        self._record(order, new_status, actor, note)
        db.commit()
        db.refresh(order)
        if new_status == OrderStatus.CANCELLED:
            self.cache.bump_catalog_version()
        log_event("ORDER_STATUS_CHANGED", user_id=actor, order_id=order.id, from_status=old, to_status=new_status)
        self.notifier.publish(Topics.BUSINESS, f"Order {order.order_number} is now {new_status}", {
            "order_id": order.id, "order_number": order.order_number, "customer_id": order.user_id, "status": new_status,
        })
        return order

    def cancel_by_customer(self, db: Session, order_id: int, scope: Scope, reason: str | None) -> Order:
        order = self.get_order(db, order_id, scope)
        if order.status != OrderStatus.PENDING_PAYMENT:
            raise ConflictError("Only orders awaiting payment can be cancelled; please contact support",
                                code="ORDER_NOT_CANCELLABLE")
        return self.change_status(db, order_id, OrderStatus.CANCELLED, scope.user_id, reason or "Cancelled by customer", scope)

    # ------------------------------------------------------------------ payments
    def create_payment(self, db: Session, order_id: int, scope: Scope) -> Payment:
        order = self.get_order(db, order_id, scope, lock=True)
        if order.status != OrderStatus.PENDING_PAYMENT:
            db.rollback()
            raise ConflictError(f"Order is {order.status} and cannot be paid", code="ORDER_NOT_PAYABLE")
        pending = next((p for p in order.payments if p.status == PaymentStatus.PENDING), None)
        if pending:
            db.commit()
            return pending  # idempotent: one open payment per order
        payment = Payment(
            payment_ref=f"PAY-{secrets.token_hex(8).upper()}", order=order, provider=self.gateway.name,
            amount=order.total, currency=order.currency, status=PaymentStatus.PENDING,
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        log_event("PAYMENT_CREATED", user_id=order.user_id, order_id=order.id, payment_id=payment.id,
                  amount=str(payment.amount))
        return payment

    def confirm_payment(self, db: Session, body: PaymentConfirmBody, scope: Scope) -> tuple[Order, Payment]:
        payment = db.scalar(
            select(Payment).where(Payment.id == body.payment_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if payment is None or (not scope.is_admin and payment.order.user_id != scope.user_id):
            raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
        order = self._lock_order(db, payment.order_id)
        assert order is not None
        if payment.status == PaymentStatus.SUCCEEDED:
            db.commit()
            return order, payment  # idempotent retry
        if payment.status != PaymentStatus.PENDING:
            db.rollback()
            raise ConflictError("This payment attempt is closed; start a new payment", code="PAYMENT_CLOSED",
                                details={"payment_status": payment.status})
        if order.status != OrderStatus.PENDING_PAYMENT:
            payment.status, payment.failure_code = PaymentStatus.FAILED, "ORDER_NOT_PAYABLE"
            payment.failure_reason = f"Order is {order.status}"
            db.commit()
            raise ConflictError(f"Order is {order.status} and cannot be paid", code="ORDER_NOT_PAYABLE")

        card = CardDetails(body.card_number, body.card_holder, body.expiry_month, body.expiry_year, body.cvv)
        result = self.gateway.charge(payment.amount, payment.currency, card, payment.payment_ref)
        payment.card_last4, payment.card_brand = result.card_last4, result.card_brand
        payment.confirmed_at = utcnow()

        if not result.success:
            payment.status = PaymentStatus.FAILED
            payment.failure_code, payment.failure_reason = result.failure_code, result.failure_reason
            db.commit()
            PAYMENTS.labels("failure").inc()
            PAYMENT_FAILURES.labels(result.failure_code or "UNKNOWN").inc()
            log_event("PAYMENT_FAILED", user_id=order.user_id, order_id=order.id, payment_id=payment.id,
                      failure_code=result.failure_code)
            raise AppError(
                result.failure_reason or "Payment declined", code="PAYMENT_DECLINED", status_code=402,
                details={"payment_id": payment.id, "order_id": order.id, "failure_code": result.failure_code,
                         "order_status": order.status, "retry_allowed": True},
            )

        # success: convert the reservation into a real stock decrement
        inventories = self._lock_inventory(db, [i.product_id for i in order.items if i.product_id])
        for item in order.items:
            inv = inventories.get(item.product_id) if item.product_id else None
            if inv is not None:
                inv.reserved = max(inv.reserved - item.quantity, 0)
                inv.quantity = max(inv.quantity - item.quantity, 0)
        payment.status = PaymentStatus.SUCCEEDED
        order.paid_at = utcnow()
        self._record(order, OrderStatus.PAID, order.user_id, f"Payment {payment.payment_ref} succeeded")
        db.commit()
        db.refresh(order)
        PAYMENTS.labels("success").inc()
        log_event("PAYMENT_SUCCESS", user_id=order.user_id, order_id=order.id, payment_id=payment.id,
                  amount=str(payment.amount), transaction_id=result.transaction_id)
        log_event("ORDER_STATUS_CHANGED", user_id=order.user_id, order_id=order.id,
                  from_status=OrderStatus.PENDING_PAYMENT, to_status=OrderStatus.PAID)
        self.cache.bump_catalog_version()

        try:
            self.invoices.ensure_invoice(db, order)
        except (StorageUnavailableError, OSError, ValueError):
            # payment is already committed; the reaper / next invoice request retries generation
            db.rollback()
            logger.warning("invoice generation deferred", extra={"order_id": order.id})
        self.notifier.publish(Topics.BUSINESS, f"Order {order.order_number} confirmed", {
            "order_id": order.id, "order_number": order.order_number, "customer_id": order.user_id,
            "total": str(order.total), "currency": order.currency, "invoice_number": order.invoice_number,
        })
        return order, payment
