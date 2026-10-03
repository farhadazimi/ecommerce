"""Background maintenance loop (safe with many replicas).

* cancels orders that stayed in PENDING_PAYMENT longer than ORDER_PAYMENT_TIMEOUT_MINUTES
  (releasing their stock reservation)
* retries invoice generation for paid orders whose invoice upload failed (e.g. OBS outage)

Rows are claimed with ``FOR UPDATE SKIP LOCKED`` so replicas never process the same order.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlalchemy import select

from ecommerce_common.db import Database
from ecommerce_common.models import Order, OrderStatus, utcnow

from .invoices import INVOICEABLE
from .orders import OrderService, Scope

logger = logging.getLogger("ecommerce.reaper")
SYSTEM = Scope(user_id=None, is_admin=True)


class OrderReaper:
    def __init__(self, db: Database, service: OrderService, timeout_minutes: int, interval_seconds: int) -> None:
        self.db = db
        self.service = service
        self.timeout = timedelta(minutes=timeout_minutes)
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="order-reaper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - keep the loop alive; DB may be briefly unavailable
                logger.warning("reaper iteration failed", exc_info=True)

    def run_once(self) -> dict[str, int]:
        expired = invoiced = 0
        cutoff = utcnow() - self.timeout
        session = self.db.session()
        try:
            ids = session.scalars(
                select(Order.id)
                .where(Order.status == OrderStatus.PENDING_PAYMENT, Order.created_at < cutoff)
                .order_by(Order.id).limit(50).with_for_update(skip_locked=True)
            ).all()
            session.commit()
            for order_id in ids:
                try:
                    self.service.change_status(session, order_id, OrderStatus.CANCELLED, None,
                                               "Payment not received in time", SYSTEM)
                    expired += 1
                except Exception:  # noqa: BLE001
                    session.rollback()
                    logger.warning("could not expire order", extra={"order_id": order_id}, exc_info=True)

            missing = session.scalars(
                select(Order).where(Order.status.in_(INVOICEABLE), Order.invoice_key.is_(None))
                .order_by(Order.id).limit(20)
            ).unique().all()
            for order in missing:
                try:
                    self.service.invoices.ensure_invoice(session, order)
                    invoiced += 1
                except Exception:  # noqa: BLE001
                    session.rollback()
                    break  # storage still down, try again next round
        finally:
            session.close()
        if expired or invoiced:
            logger.info("reaper run", extra={"expired_orders": expired, "invoices_generated": invoiced})
        return {"expired": expired, "invoiced": invoiced}
