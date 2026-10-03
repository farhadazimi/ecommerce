"""End-to-end test against a RUNNING deployment (docker compose, kind, staging, production).

    E2E_API_URL=http://localhost:8000 E2E_FRONTEND_URL=http://localhost:8080 pytest tests/e2e -m e2e
    E2E_API_URL=http://shop.localtest.me E2E_FRONTEND_URL=http://shop.localtest.me pytest tests/e2e -m e2e

Uses the seeded admin account (E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD) and registers a fresh
customer, so it is safe to run repeatedly.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

API = os.environ.get("E2E_API_URL", "").rstrip("/")
FRONTEND = os.environ.get("E2E_FRONTEND_URL", "").rstrip("/")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "Admin123!")

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not API, reason="E2E_API_URL not set")]

SHIPPING = {
    "shipping_name": "E2E Customer", "shipping_address_line1": "1 Cloud Street", "shipping_city": "Tehran",
    "shipping_postal_code": "10001", "shipping_country": "Iran",
}
CARD = {"card_holder": "E2E Customer", "expiry_month": 12, "expiry_year": 2035, "cvv": "123"}


@pytest.fixture(scope="module")
def http() -> httpx.Client:
    with httpx.Client(base_url=API, timeout=20, follow_redirects=False) as client:
        yield client


def bearer(http: httpx.Client, email: str, password: str) -> dict[str, str]:
    r = http.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    http.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health_endpoints(http):
    assert http.get("/api/health").json()["status"] == "ok"
    if FRONTEND:
        f = httpx.get(f"{FRONTEND}/health", timeout=10)
        assert f.status_code == 200 and f.json()["service"] == "frontend"
        cfg = httpx.get(f"{FRONTEND}/config.js", timeout=10)
        assert cfg.status_code == 200 and "__APP_CONFIG__" in cfg.text
        page = httpx.get(f"{FRONTEND}/products/1", timeout=10)
        assert page.status_code == 200 and '<div id="root">' in page.text


def test_catalog_images_served_from_object_storage(http):
    products = http.get("/api/products?page_size=5").json()
    assert products["total"] >= 1
    with_image = [p for p in products["items"] if p["image_url"]]
    assert with_image, "seeded products should have images in OBS"
    img = httpx.get(with_image[0]["image_url"], timeout=10)
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/")


def test_complete_customer_flow(http):
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    r = http.post("/api/auth/register", json={"email": email, "password": "E2ePass1234", "full_name": "E2E Customer"})
    assert r.status_code == 201, r.text
    customer = bearer(http, email, "E2ePass1234")

    product = next(p for p in http.get("/api/products?in_stock=true&sort=price_asc").json()["items"]
                   if p["stock"]["available"] >= 2)
    detail = http.get(f"/api/products/{product['id']}").json()
    before = detail["stock"]["available"]

    cart = http.post("/api/cart/items", json={"product_id": product["id"], "quantity": 1}, headers=customer).json()
    item = cart["items"][0]
    cart = http.put(f"/api/cart/items/{item['id']}", json={"quantity": 2}, headers=customer).json()
    assert cart["item_count"] == 2

    order = http.post("/api/orders", json=SHIPPING, headers={**customer, "Idempotency-Key": uuid.uuid4().hex})
    assert order.status_code == 201, order.text
    order = order.json()
    assert order["status"] == "PENDING_PAYMENT"

    pay = http.post("/api/payment/create", json={"order_id": order["id"]}, headers=customer).json()
    declined = http.post("/api/payment/confirm", json={"payment_id": pay["id"], "card_number": "4000000000000002", **CARD},
                         headers=customer)
    assert declined.status_code == 402 and declined.json()["error"]["code"] == "PAYMENT_DECLINED"

    pay = http.post("/api/payment/create", json={"order_id": order["id"]}, headers=customer).json()
    ok = http.post("/api/payment/confirm", json={"payment_id": pay["id"], "card_number": "4242424242424242", **CARD},
                   headers=customer)
    assert ok.status_code == 200, ok.text
    assert ok.json()["order"]["status"] == "PAID"
    assert http.get(f"/api/products/{product['id']}").json()["stock"]["available"] == before - 2

    invoice = http.get(f"/api/orders/{order['id']}/invoice", headers=customer)
    assert invoice.status_code == 200, invoice.text
    pdf = httpx.get(invoice.json()["url"], timeout=10)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    history = http.get("/api/orders", headers=customer).json()
    assert history["items"][0]["id"] == order["id"]

    admin = bearer(http, ADMIN_EMAIL, ADMIN_PASSWORD)
    found = http.get(f"/api/admin/orders?q={order['order_number']}", headers=admin).json()
    assert found["total"] == 1
    for status in ("PROCESSING", "SHIPPED"):
        r = http.put(f"/api/admin/orders/{order['id']}/status", json={"status": status}, headers=admin)
        assert r.status_code == 200 and r.json()["status"] == status
    assert http.get(f"/api/orders/{order['id']}", headers=customer).json()["status"] == "SHIPPED"
    assert http.get("/api/admin/statistics", headers=admin).json()["orders"] >= 1


def test_internal_surfaces_not_exposed(http):
    """The Order Service API and Prometheus metrics must not be reachable through the public entrypoint.
    (Behind the Ingress unknown paths fall through to the SPA, which answers with index.html.)"""
    behind_ingress = bool(FRONTEND) and FRONTEND == API  # compose publishes the backend port directly
    paths = ["/internal/orders", "/api/internal/orders"] + (["/metrics", "/api/metrics"] if behind_ingress else [])
    for path in paths:
        r = http.get(path)
        assert "INVALID_SERVICE_TOKEN" not in r.text and "http_requests_total" not in r.text, path
        assert r.status_code in (200, 401, 404) and (r.status_code != 200 or "text/html" in r.headers["content-type"]), path
    assert http.get("/api/admin/users").status_code == 401
