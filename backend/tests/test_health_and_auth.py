from __future__ import annotations

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD, CUSTOMER_EMAIL, CUSTOMER_PASSWORD, login


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok" and r.json()["service"] == "backend-api"
        assert client.get("/api/health").status_code == 200

    def test_ready_checks_dependencies(self, client):
        r = client.get("/ready")
        assert r.status_code == 200
        assert r.json()["checks"]["database"]["status"] == "ok"

    def test_ready_fails_when_database_down(self, client, backend_app):
        def broken():
            raise ConnectionError("db down")

        original = backend_app.state.db.ping
        backend_app.state.db.ping = broken
        try:
            r = client.get("/ready")
            assert r.status_code == 503 and r.json()["checks"]["database"]["status"] == "error"
        finally:
            backend_app.state.db.ping = original

    def test_metrics_exposed(self, client):
        client.get("/health")
        body = client.get("/metrics").text
        assert "http_requests_total" in body and "http_request_duration_seconds" in body

    def test_security_headers_and_request_id(self, client):
        r = client.get("/api/categories", headers={"X-Request-ID": "req-12345678"})
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-request-id"] == "req-12345678"
        assert "strict-transport-security" not in r.headers
        https = client.get("/api/categories", headers={"X-Forwarded-Proto": "https"})
        # TestClient peer is not in TRUSTED_PROXIES, so the header must be ignored
        assert "strict-transport-security" not in https.headers

    def test_openapi_docs_available(self, client):
        spec = client.get("/api/openapi.json").json()
        for path in ("/api/auth/register", "/api/products", "/api/cart/items", "/api/orders", "/api/payment/confirm",
                     "/api/admin/statistics", "/health", "/ready"):
            assert path in spec["paths"], path

    def test_cors_preflight(self, client):
        r = client.options("/api/products", headers={"Origin": "http://localhost:8080", "Access-Control-Request-Method": "GET"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:8080"
        assert r.headers["access-control-allow-credentials"] == "true"
        r = client.options("/api/products", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in r.headers

    def test_unknown_route_uses_error_envelope(self, client):
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


class TestAuth:
    def test_register_login_me_logout(self, client, clean_db):
        r = client.post("/api/auth/register", json={"email": "New.User@Example.com", "password": "Passw0rd!", "full_name": "New User"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["user"]["email"] == "new.user@example.com" and body["user"]["role"] == "CUSTOMER"
        assert "password" not in r.text and "password_hash" not in r.text
        assert "ecom_session" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]

        # cookie-based session works (browser flow)
        assert client.get("/api/auth/me").json()["email"] == "new.user@example.com"
        client.cookies.clear()

        token = client.post("/api/auth/login", json={"email": "new.user@example.com", "password": "Passw0rd!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        r = client.get("/api/auth/me", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_REVOKED"

    def test_duplicate_registration(self, client, customer_user):
        r = client.post("/api/auth/register", json={"email": CUSTOMER_EMAIL, "password": "Passw0rd1", "full_name": "Dup"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_TAKEN"

    def test_registration_validation(self, client, clean_db):
        r = client.post("/api/auth/register", json={"email": "not-an-email", "password": "short", "full_name": "<b>x</b>"})
        assert r.status_code == 422
        err = r.json()["error"]
        assert err["code"] == "VALIDATION_ERROR"
        fields = {d["field"] for d in err["details"]}
        assert {"email", "password", "full_name"} <= fields
        assert "short" not in r.text  # submitted values are never echoed back

    def test_invalid_credentials(self, client, customer_user):
        r = client.post("/api/auth/login", json={"email": CUSTOMER_EMAIL, "password": "wrong-password1"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
        r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password1"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_missing_invalid_and_expired_tokens(self, client, settings, customer_user):
        assert client.get("/api/auth/me").json()["error"]["code"] == "UNAUTHENTICATED"
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
        from ecommerce_common.security import create_access_token

        expired_settings = settings.model_copy(update={"jwt_expires_minutes": -5})
        token, _ = create_access_token(expired_settings, customer_user, "CUSTOMER")
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"

    def test_deactivated_user_rejected(self, client, admin_headers, customer_user):
        headers = login(client, CUSTOMER_EMAIL, CUSTOMER_PASSWORD)
        r = client.put(f"/api/admin/users/{customer_user}/status", json={"is_active": False}, headers=admin_headers)
        assert r.status_code == 200
        assert client.get("/api/auth/me", headers=headers).json()["error"]["code"] == "ACCOUNT_INACTIVE"
        r = client.post("/api/auth/login", json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD})
        assert r.json()["error"]["code"] == "ACCOUNT_INACTIVE"

    def test_rate_limit_on_login(self, client, backend_app, customer_user):
        backend_app.state.settings = backend_app.state.settings.model_copy(
            update={"rate_limit_enabled": True, "rate_limit_auth_per_minute": 3}
        )
        codes = [client.post("/api/auth/login", json={"email": CUSTOMER_EMAIL, "password": "bad-password1"}).status_code
                 for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]

    def test_admin_login(self, client, admin_user):
        r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.json()["user"]["role"] == "ADMIN"
