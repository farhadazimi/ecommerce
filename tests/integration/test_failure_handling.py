"""Failure handling (requirements §24): unavailable dependencies return clean 503s, never stack traces."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from conftest import SHIPPING
from ecommerce_common.cache import CacheService, RedisCache
from ecommerce_common.config import Settings
from ecommerce_common.db import Database


def test_database_unavailable_returns_503(settings, cache, storage):
    from backend_api.main import create_app

    broken = settings.model_copy(update={"database_url": "mysql+pymysql://u:p@127.0.0.1:1/x", "database_connect_timeout": 1})
    app = create_app(broken, database=Database(broken), cache=cache, storage=storage)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/products")
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["code"] == "DATABASE_UNAVAILABLE" and "Traceback" not in r.text and "pymysql" not in r.text
    assert r.headers["retry-after"] == "5"
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200  # liveness stays green: don't restart pods for an RDS outage


def test_redis_unavailable_degrades_to_database(settings, clean_db, storage, make_product):
    """DCS outage: catalog still served from RDS; readiness only fails if REDIS_REQUIRED=true."""
    from backend_api.main import create_app

    make_product("Still visible")
    s = settings.model_copy(update={"redis_host": "127.0.0.1", "redis_port": 1, "redis_socket_timeout": 0.2})
    broken_cache = CacheService(RedisCache(s), "t")
    client = TestClient(create_app(s, database=clean_db, cache=broken_cache, storage=storage))
    assert client.get("/api/products").json()["total"] == 1
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["redis"]["status"] == "error"

    required = s.model_copy(update={"redis_required": True})
    client = TestClient(create_app(required, database=clean_db, cache=broken_cache, storage=storage))
    assert client.get("/ready").status_code == 503


def test_order_service_unavailable_returns_503(settings, clean_db, cache, storage, customer_user, make_product):
    from backend_api.main import create_app
    from backend_api.services.order_client import OrderServiceClient

    s = settings.model_copy(update={"order_service_url": "http://127.0.0.1:1", "order_service_timeout": 1.0})
    app = create_app(s, database=clean_db, cache=cache, storage=storage, order_client=OrderServiceClient(s))
    client = TestClient(app)
    token = client.post("/api/auth/login", json={"email": "customer@example.com", "password": "CustomerPass123"}).json()["access_token"]
    client.cookies.clear()
    r = client.get("/api/orders", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "ORDER_SERVICE_UNAVAILABLE"


def test_storage_unavailable_on_upload(client, admin_headers, make_product, storage, monkeypatch):
    from conftest import png_bytes
    from ecommerce_common.storage import StorageUnavailableError

    pid = make_product("Cam")

    def down(*a, **k):
        raise StorageUnavailableError("Object storage is unavailable")

    monkeypatch.setattr(storage, "upload", down)
    r = client.post(f"/api/products/{pid}/images", files={"file": ("a.png", png_bytes(), "image/png")}, headers=admin_headers)
    assert r.status_code == 503 and r.json()["error"]["code"] == "STORAGE_UNAVAILABLE"


def test_invalid_requests_and_not_found(client, customer_headers):
    assert client.get("/api/orders/424242", headers=customer_headers).json()["error"]["code"] == "ORDER_NOT_FOUND"
    r = client.post("/api/orders", json={**SHIPPING, "shipping_city": ""}, headers=customer_headers)
    assert r.status_code == 422
    r = client.post("/api/orders", json=SHIPPING, headers={**customer_headers, "Idempotency-Key": "bad key!"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"
    r = client.post("/api/payment/confirm", json={"payment_id": 1, "card_number": "abc"}, headers=customer_headers)
    assert r.status_code == 422
    r = client.post("/api/products", content=b"{not json", headers={**customer_headers, "Content-Type": "application/json"})
    assert r.status_code in (403, 422)


def test_unhandled_errors_do_not_leak(settings, clean_db, cache, storage):
    from backend_api.main import create_app

    app = create_app(settings, database=clean_db, cache=cache, storage=storage)

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    r = TestClient(app, raise_server_exceptions=False).get("/api/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in r.text


def test_order_client_retries_only_safe_calls(settings):
    from backend_api.services.order_client import OrderServiceClient

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    client = OrderServiceClient(settings, client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x"))
    for method, safe, expected in (("GET", None, 3), ("POST", None, 1), ("POST", True, 3)):
        calls["n"] = 0
        try:
            client.request(method, "/internal/orders", user=None, retry_safe=safe)
        except Exception as exc:
            assert getattr(exc, "code", "") == "ORDER_SERVICE_UNAVAILABLE"
        assert calls["n"] == expected, (method, safe)


def test_settings_type_is_shared():
    assert Settings.model_fields["redis_required"].default is False
