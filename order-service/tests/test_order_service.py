"""Order Service internal API: service auth, order placement, inventory, payments, invoices, lifecycle."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from conftest import CARD_DECLINED, CARD_OK, SHIPPING
from ecommerce_common.models import Cart, CartItem, Inventory, Order, OrderStatus, utcnow


def internal(settings, user_id: int | None, role: str = "CUSTOMER") -> dict[str, str]:
    h = {"X-Internal-Token": settings.internal_service_token}
    if user_id is not None:
        h.update({"X-User-Id": str(user_id), "X-User-Role": role})
    return h


def fill_cart(db, user_id: int, items: dict[int, int]) -> None:
    with db.transaction() as s:
        cart = s.scalar(select(Cart).where(Cart.user_id == user_id)) or Cart(user_id=user_id)
        s.add(cart)
        s.flush()
        for pid, qty in items.items():
            s.add(CartItem(cart_id=cart.id, product_id=pid, quantity=qty))


def stock(db, pid: int) -> tuple[int, int]:
    with db.transaction() as s:
        inv = s.get(Inventory, pid)
        return inv.quantity, inv.reserved


class TestServiceAuth:
    def test_requires_internal_token(self, order_http, settings):
        r = order_http.get("/internal/orders", headers={"X-User-Id": "1", "X-User-Role": "ADMIN"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_SERVICE_TOKEN"
        r = order_http.get("/internal/orders", headers={"X-Internal-Token": "wrong", "X-User-Id": "1"})
        assert r.status_code == 401

    def test_requires_user_context(self, order_http, settings):
        assert order_http.get("/internal/orders", headers=internal(settings, None)).status_code == 401

    def test_health_and_ready(self, order_http):
        assert order_http.get("/health").json()["service"] == "order-service"
        assert order_http.get("/ready").status_code == 200


class TestPlaceOrder:
    def test_place_order_reserves_stock_and_clears_cart(self, order_http, settings, clean_db, customer_user, make_product):
        p1, p2 = make_product("A", "30.00", stock=5), make_product("B", "80.00", stock=3)
        fill_cart(clean_db, customer_user, {p1: 2, p2: 1})
        r = order_http.post("/internal/orders", json=SHIPPING, headers=internal(settings, customer_user))
        assert r.status_code == 201, r.text
        order = r.json()
        assert order["status"] == "PENDING_PAYMENT" and order["subtotal"] == 140.0
        assert order["shipping_fee"] == 0.0 and order["total"] == 140.0 and order["item_count"] == 3
        assert order["order_number"].startswith("ORD-")
        assert stock(clean_db, p1) == (5, 2) and stock(clean_db, p2) == (3, 1)
        with clean_db.transaction() as s:
            assert s.scalars(select(CartItem)).all() == []

    def test_empty_cart(self, order_http, settings, customer_user):
        r = order_http.post("/internal/orders", json=SHIPPING, headers=internal(settings, customer_user))
        assert r.status_code == 400 and r.json()["error"]["code"] == "CART_EMPTY"

    def test_insufficient_inventory_rolls_back(self, order_http, settings, clean_db, customer_user, make_product):
        p1, p2 = make_product("A", stock=5), make_product("B", stock=1)
        fill_cart(clean_db, customer_user, {p1: 1, p2: 2})
        r = order_http.post("/internal/orders", json=SHIPPING, headers=internal(settings, customer_user))
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "INSUFFICIENT_INVENTORY" and err["details"][0]["product_id"] == p2
        assert stock(clean_db, p1) == (5, 0)  # nothing reserved
        with clean_db.transaction() as s:
            assert len(s.scalars(select(CartItem)).all()) == 2  # cart untouched

    def test_idempotency_key_returns_same_order(self, order_http, settings, clean_db, customer_user, make_product):
        pid = make_product("A", stock=5)
        fill_cart(clean_db, customer_user, {pid: 1})
        body = {**SHIPPING, "idempotency_key": "checkout-abc-123"}
        first = order_http.post("/internal/orders", json=body, headers=internal(settings, customer_user))
        second = order_http.post("/internal/orders", json=body, headers=internal(settings, customer_user))
        assert first.status_code == 201 and second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert stock(clean_db, pid) == (5, 1)

    def test_customers_only_see_their_orders(self, order_http, settings, clean_db, customer_user, admin_user, make_product):
        pid = make_product("A", stock=5)
        fill_cart(clean_db, customer_user, {pid: 1})
        oid = order_http.post("/internal/orders", json=SHIPPING, headers=internal(settings, customer_user)).json()["id"]
        other = admin_user + customer_user + 100
        assert order_http.get(f"/internal/orders/{oid}", headers=internal(settings, other)).status_code == 404
        assert order_http.get("/internal/orders", headers=internal(settings, other)).json()["total"] == 0
        # spoofing another user via query param is ignored for customers
        assert order_http.get(f"/internal/orders?user_id={customer_user}", headers=internal(settings, other)).json()["total"] == 0
        assert order_http.get(f"/internal/orders/{oid}", headers=internal(settings, admin_user, "ADMIN")).status_code == 200
        assert order_http.get("/internal/orders/999999", headers=internal(settings, admin_user, "ADMIN")).json()["error"]["code"] == "ORDER_NOT_FOUND"


class TestPayments:
    def _order(self, order_http, settings, db, user_id, make_product, qty=2, price="10.00", stock_qty=5):
        pid = make_product("Paid thing", price, stock=stock_qty)
        fill_cart(db, user_id, {pid: qty})
        order = order_http.post("/internal/orders", json=SHIPPING, headers=internal(settings, user_id)).json()
        return pid, order

    def test_success_flow_decrements_stock_and_generates_invoice(self, order_http, settings, clean_db, customer_user,
                                                                  make_product, storage):
        pid, order = self._order(order_http, settings, clean_db, customer_user, make_product)
        h = internal(settings, customer_user)
        payment = order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=h).json()
        assert payment["status"] == "PENDING" and payment["amount"] == order["total"]
        # creating again returns the same open payment
        assert order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=h).json()["id"] == payment["id"]

        r = order_http.post("/internal/payments/confirm", json={"payment_id": payment["id"], **CARD_OK}, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payment"]["status"] == "SUCCEEDED" and body["payment"]["card_last4"] == "4242"
        assert body["order"]["status"] == "PAID" and body["order"]["invoice"]["available"]
        assert stock(clean_db, pid) == (3, 0)

        with clean_db.transaction() as s:
            o = s.get(Order, order["id"])
            assert o.invoice_key.startswith(f"invoices/{o.paid_at:%Y}/{o.paid_at:%m}/INV-")
            pdf = storage.download(o.invoice_key)
        assert pdf.startswith(b"%PDF") and len(pdf) > 1000

        # confirm is idempotent
        again = order_http.post("/internal/payments/confirm", json={"payment_id": payment["id"], **CARD_OK}, headers=h)
        assert again.status_code == 200 and again.json()["payment"]["status"] == "SUCCEEDED"
        assert stock(clean_db, pid) == (3, 0)

        inv = order_http.get(f"/internal/orders/{order['id']}/invoice", headers=h).json()
        assert inv["invoice_number"].startswith("INV-") and "/api/files/invoices/" in inv["url"]

        # a paid order cannot be paid again
        r = order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=h)
        assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_PAYABLE"

    def test_declined_payment_keeps_order_payable(self, order_http, settings, clean_db, customer_user, make_product):
        pid, order = self._order(order_http, settings, clean_db, customer_user, make_product)
        h = internal(settings, customer_user)
        pay = order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=h).json()
        r = order_http.post("/internal/payments/confirm", json={"payment_id": pay["id"], **CARD_DECLINED}, headers=h)
        assert r.status_code == 402
        err = r.json()["error"]
        assert err["code"] == "PAYMENT_DECLINED" and err["details"]["failure_code"] == "CARD_DECLINED"
        assert stock(clean_db, pid) == (5, 2)  # still reserved, not decremented
        # the failed attempt is closed; a new attempt succeeds
        r = order_http.post("/internal/payments/confirm", json={"payment_id": pay["id"], **CARD_OK}, headers=h)
        assert r.status_code == 409 and r.json()["error"]["code"] == "PAYMENT_CLOSED"
        pay2 = order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=h).json()
        assert pay2["id"] != pay["id"]
        r = order_http.post("/internal/payments/confirm", json={"payment_id": pay2["id"], **CARD_OK}, headers=h)
        assert r.status_code == 200 and r.json()["order"]["status"] == "PAID"
        statuses = [p["status"] for p in order_http.get(f"/internal/orders/{order['id']}", headers=h).json()["payments"]]
        assert statuses == ["FAILED", "SUCCEEDED"]

    def test_invoice_deferred_when_storage_down(self, order_http, order_app, settings, clean_db, customer_user, make_product,
                                                storage, monkeypatch):
        from ecommerce_common.storage import StorageUnavailableError

        _, order = self._order(order_http, settings, clean_db, customer_user, make_product)
        h = internal(settings, customer_user)
        pay = order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=h).json()

        def down(*a, **k):
            raise StorageUnavailableError("OBS down")

        monkeypatch.setattr(storage, "upload", down)
        r = order_http.post("/internal/payments/confirm", json={"payment_id": pay["id"], **CARD_OK}, headers=h)
        assert r.status_code == 200 and r.json()["order"]["status"] == "PAID"
        assert r.json()["order"]["invoice"]["available"] is False
        r = order_http.get(f"/internal/orders/{order['id']}/invoice", headers=h)
        assert r.status_code == 503 and r.json()["error"]["code"] == "STORAGE_UNAVAILABLE"

        monkeypatch.undo()  # OBS is back: the reaper generates the missing invoice
        result = order_app.state.reaper.run_once()
        assert result["invoiced"] == 1
        assert order_http.get(f"/internal/orders/{order['id']}", headers=h).json()["invoice"]["available"] is True

    def test_other_user_cannot_pay(self, order_http, settings, clean_db, customer_user, admin_user, make_product):
        _, order = self._order(order_http, settings, clean_db, customer_user, make_product)
        pay = order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=internal(settings, customer_user)).json()
        stranger = internal(settings, customer_user + admin_user + 50)
        assert order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=stranger).status_code == 404
        r = order_http.post("/internal/payments/confirm", json={"payment_id": pay["id"], **CARD_OK}, headers=stranger)
        assert r.status_code == 404 and r.json()["error"]["code"] == "PAYMENT_NOT_FOUND"


class TestLifecycle:
    def _paid_order(self, order_http, settings, db, user_id, make_product):
        pid = make_product("Thing", "10.00", stock=10)
        fill_cart(db, user_id, {pid: 3})
        h = internal(settings, user_id)
        order = order_http.post("/internal/orders", json=SHIPPING, headers=h).json()
        pay = order_http.post("/internal/payments", json={"order_id": order["id"]}, headers=h).json()
        order_http.post("/internal/payments/confirm", json={"payment_id": pay["id"], **CARD_OK}, headers=h)
        return pid, order["id"]

    def test_admin_status_transitions(self, order_http, settings, clean_db, customer_user, admin_user, make_product):
        _, oid = self._paid_order(order_http, settings, clean_db, customer_user, make_product)
        admin = internal(settings, admin_user, "ADMIN")
        for status in ("PROCESSING", "SHIPPED", "DELIVERED"):
            r = order_http.put(f"/internal/orders/{oid}/status", json={"status": status}, headers=admin)
            assert r.status_code == 200 and r.json()["status"] == status
        r = order_http.put(f"/internal/orders/{oid}/status", json={"status": "CANCELLED"}, headers=admin)
        assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"
        history = order_http.get(f"/internal/orders/{oid}", headers=admin).json()["history"]
        assert [h["to_status"] for h in history] == ["PENDING_PAYMENT", "PAID", "PROCESSING", "SHIPPED", "DELIVERED"]
        # customers cannot change status
        r = order_http.put(f"/internal/orders/{oid}/status", json={"status": "SHIPPED"}, headers=internal(settings, customer_user))
        assert r.status_code == 403

    def test_cancel_paid_order_restocks_and_refunds(self, order_http, settings, clean_db, customer_user, admin_user, make_product):
        pid, oid = self._paid_order(order_http, settings, clean_db, customer_user, make_product)
        assert stock(clean_db, pid) == (7, 0)
        r = order_http.put(f"/internal/orders/{oid}/status", json={"status": "CANCELLED", "note": "out of area"},
                           headers=internal(settings, admin_user, "ADMIN"))
        assert r.json()["status"] == "CANCELLED" and r.json()["payments"][0]["status"] == "REFUNDED"
        assert stock(clean_db, pid) == (10, 0)

    def test_customer_cancel_pending_releases_reservation(self, order_http, settings, clean_db, customer_user, make_product):
        pid = make_product("Thing", stock=4)
        fill_cart(clean_db, customer_user, {pid: 4})
        h = internal(settings, customer_user)
        oid = order_http.post("/internal/orders", json=SHIPPING, headers=h).json()["id"]
        assert stock(clean_db, pid) == (4, 4)
        r = order_http.post(f"/internal/orders/{oid}/cancel", json={"reason": "changed my mind"}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
        assert stock(clean_db, pid) == (4, 0)
        r = order_http.post(f"/internal/orders/{oid}/cancel", json={}, headers=h)
        assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_CANCELLABLE"

    def test_reaper_expires_unpaid_orders(self, order_http, order_app, settings, clean_db, customer_user, make_product):
        pid = make_product("Thing", stock=4)
        fill_cart(clean_db, customer_user, {pid: 2})
        oid = order_http.post("/internal/orders", json=SHIPPING, headers=internal(settings, customer_user)).json()["id"]
        with clean_db.transaction() as s:
            s.execute(update(Order).where(Order.id == oid).values(created_at=utcnow() - timedelta(hours=2)))
        assert order_app.state.reaper.run_once()["expired"] == 1
        with clean_db.transaction() as s:
            assert s.get(Order, oid).status == OrderStatus.CANCELLED
        assert stock(clean_db, pid) == (4, 0)

    def test_admin_list_filters(self, order_http, settings, clean_db, customer_user, admin_user, make_product):
        self._paid_order(order_http, settings, clean_db, customer_user, make_product)
        admin = internal(settings, admin_user, "ADMIN")
        assert order_http.get("/internal/orders", headers=admin).json()["total"] == 1
        assert order_http.get("/internal/orders?status=PAID", headers=admin).json()["total"] == 1
        assert order_http.get("/internal/orders?status=SHIPPED", headers=admin).json()["total"] == 0
        assert order_http.get("/internal/orders?q=customer@example", headers=admin).json()["total"] == 1
