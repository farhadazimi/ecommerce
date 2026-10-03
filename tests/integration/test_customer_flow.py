"""The mandatory end-to-end customer flow (requirements §23), executed through the public
Backend API, which delegates to the Order Service over HTTP (in-process transport)."""

from __future__ import annotations

import uuid

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD, CARD_DECLINED, CARD_OK, SHIPPING, login


def test_complete_customer_flow(client, admin_user, make_product, storage):
    phone = make_product("Smartphone", "250.00", stock=10, category="Electronics")
    make_product("Case", "15.00", stock=50, category="Electronics")

    # 1-3: open site, register, login
    assert client.get("/health").status_code == 200
    r = client.post("/api/auth/register", json={"email": "flow@example.com", "password": "FlowPass123", "full_name": "Flow User"})
    assert r.status_code == 201
    headers = login(client, "flow@example.com", "FlowPass123")

    # 4-5: browse, open details
    listing = client.get("/api/products?category=electronics").json()
    assert listing["total"] == 2
    details = client.get(f"/api/products/{phone}").json()
    assert details["stock"]["available"] == 10

    # 6-7: add to cart, change quantity
    cart = client.post("/api/cart/items", json={"product_id": phone, "quantity": 1}, headers=headers).json()
    item_id = cart["items"][0]["id"]
    cart = client.put(f"/api/cart/items/{item_id}", json={"quantity": 2}, headers=headers).json()
    assert cart["subtotal"] == 500.0 and cart["total"] == 500.0

    # 8-9: checkout -> order placed (stock reserved)
    idem = {"Idempotency-Key": uuid.uuid4().hex}
    r = client.post("/api/orders", json=SHIPPING, headers={**headers, **idem})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "PENDING_PAYMENT" and order["total"] == 500.0
    assert client.get(f"/api/products/{phone}").json()["stock"]["available"] == 8
    assert client.get("/api/cart", headers=headers).json()["items"] == []
    # a retried checkout with the same key does not create a duplicate
    assert client.post("/api/orders", json=SHIPPING, headers={**headers, **idem}).json()["id"] == order["id"]

    # 10: payment simulation - first a declined card, then success
    payment = client.post("/api/payment/create", json={"order_id": order["id"]}, headers=headers).json()
    r = client.post("/api/payment/confirm", json={"payment_id": payment["id"], **CARD_DECLINED}, headers=headers)
    assert r.status_code == 402 and r.json()["error"]["code"] == "PAYMENT_DECLINED"
    payment = client.post("/api/payment/create", json={"order_id": order["id"]}, headers=headers).json()
    r = client.post("/api/payment/confirm", json={"payment_id": payment["id"], **CARD_OK}, headers=headers)
    assert r.status_code == 200, r.text

    # 11-13: payment succeeds, order confirmed, inventory reduced
    confirmed = r.json()["order"]
    assert r.json()["payment"]["status"] == "SUCCEEDED" and confirmed["status"] == "PAID"
    assert client.get(f"/api/products/{phone}").json()["stock"]["available"] == 8

    # 14-15: invoice generated and stored in object storage
    assert confirmed["invoice"]["available"] and confirmed["invoice"]["number"].startswith("INV-")
    invoice = client.get(f"/api/orders/{order['id']}/invoice", headers=headers).json()
    pdf = client.get(invoice["url"].replace("http://testserver", ""))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-disposition"].startswith("attachment")

    # 16-17: confirmation + order history
    detail = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    # timestamps carry an explicit UTC offset so browsers render the customer's local time
    assert detail["created_at"].endswith("+00:00") and detail["paid_at"].endswith("+00:00")
    assert client.get("/api/auth/me", headers=headers).json()["created_at"].endswith("Z")
    assert detail["status"] == "PAID" and len(detail["items"]) == 1 and detail["items"][0]["quantity"] == 2
    history = client.get("/api/orders", headers=headers).json()
    assert history["total"] == 1 and history["items"][0]["order_number"] == order["order_number"]

    # 18-19: admin sees and updates the order
    admin = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    admin_orders = client.get("/api/admin/orders", headers=admin).json()
    assert any(o["id"] == order["id"] for o in admin_orders["items"])
    r = client.put(f"/api/admin/orders/{order['id']}/status", json={"status": "PROCESSING"}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "PROCESSING"
    r = client.put(f"/api/admin/orders/{order['id']}/status", json={"status": "SHIPPED", "note": "DHL 123"}, headers=admin)
    assert r.json()["status"] == "SHIPPED"
    assert client.get(f"/api/orders/{order['id']}", headers=headers).json()["status"] == "SHIPPED"

    stats = client.get("/api/admin/statistics", headers=admin).json()
    assert stats["orders"] == 1 and stats["revenue"] == 500.0 and stats["orders_by_status"]["SHIPPED"] == 1
    # customers cannot reach admin order APIs
    assert client.put(f"/api/admin/orders/{order['id']}/status", json={"status": "DELIVERED"}, headers=headers).status_code == 403


def test_checkout_race_for_last_items(client, customer_user, admin_user, make_product):
    """Two customers hold the last unit in their carts; only the first checkout succeeds."""
    from conftest import CUSTOMER_EMAIL, CUSTOMER_PASSWORD

    pid = make_product("Last one", stock=1)
    c1 = login(client, CUSTOMER_EMAIL, CUSTOMER_PASSWORD)
    c2 = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    client.post("/api/cart/items", json={"product_id": pid}, headers=c1)
    client.post("/api/cart/items", json={"product_id": pid}, headers=c2)
    assert client.post("/api/orders", json=SHIPPING, headers=c1).status_code == 201
    r = client.post("/api/orders", json=SHIPPING, headers=c2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"
    assert client.get("/api/cart", headers=c2).json()["warnings"]


def test_customer_cancels_pending_order(client, customer_headers, make_product):
    pid = make_product("Cancelable", stock=3)
    client.post("/api/cart/items", json={"product_id": pid, "quantity": 3}, headers=customer_headers)
    order = client.post("/api/orders", json=SHIPPING, headers=customer_headers).json()
    assert client.get(f"/api/products/{pid}").json()["stock"]["available"] == 0
    r = client.post(f"/api/orders/{order['id']}/cancel", json={"reason": "oops"}, headers=customer_headers)
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert client.get(f"/api/products/{pid}").json()["stock"]["available"] == 3
