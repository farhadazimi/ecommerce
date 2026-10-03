"""Unit tests for the shared library: config, security, cache, storage, middleware, logging."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import redis
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ecommerce_common import security
from ecommerce_common.cache import CacheService, MemoryCache, RedisCache
from ecommerce_common.config import Settings
from ecommerce_common.errors import AuthenticationError
from ecommerce_common.log import JsonFormatter, mask_sensitive
from ecommerce_common.middleware import ProxyHeadersMiddleware, RequestContextMiddleware
from ecommerce_common.pricing import money, shipping_fee
from ecommerce_common.storage import (
    LocalStorageService,
    ObjectNotFoundError,
    S3StorageService,
    invoice_key,
    product_image_key,
)
from ecommerce_common.storage.base import validate_key


# ------------------------------------------------------------------ config
class TestConfig:
    def test_development_defaults_are_accepted(self):
        s = Settings(app_env="development")
        assert s.storage_backend == "local"

    def test_production_rejects_placeholder_secrets(self):
        with pytest.raises(ValueError) as exc:
            Settings(app_env="production")
        msg = str(exc.value)
        assert "JWT_SECRET" in msg and "INTERNAL_SERVICE_TOKEN" in msg and "STORAGE_BACKEND" in msg and "REDIS_HOST" in msg

    def test_production_accepts_complete_configuration(self):
        s = Settings(
            app_env="production", jwt_secret="x" * 48, internal_service_token="y" * 32, database_password="real-pw",
            storage_backend="s3", obs_access_key="AK", obs_secret_key="SK", obs_bucket="b", redis_host="dcs.internal",
        )
        assert s.is_production_like

    def test_database_url_built_from_parts_and_escaped(self):
        s = Settings(database_host="rds.internal", database_user="app", database_password="p@ss:w/rd", database_name="shop")
        assert s.sqlalchemy_url == "mysql+pymysql://app:p%40ss%3Aw%2Frd@rds.internal:3306/shop?charset=utf8mb4"

    def test_bucket_fallbacks(self):
        s = Settings(obs_bucket="default")
        assert s.images_bucket == "default" and s.invoices_bucket == "default"
        s = Settings(obs_bucket="default", obs_bucket_images="ecommerce-images", obs_bucket_invoices="ecommerce-invoices")
        assert s.images_bucket == "ecommerce-images" and s.invoices_bucket == "ecommerce-invoices"


# ------------------------------------------------------------------ security
class TestSecurity:
    def test_password_hash_roundtrip(self):
        h = security.hash_password("Secret123")
        assert h != "Secret123"
        assert security.verify_password("Secret123", h)
        assert not security.verify_password("wrong", h)
        assert not security.verify_password("x", "not-a-bcrypt-hash")

    def test_token_roundtrip(self, settings):
        token, claims = security.create_access_token(settings, 42, "CUSTOMER")
        decoded = security.decode_access_token(settings, token)
        assert decoded.user_id == 42 and decoded.role == "CUSTOMER" and decoded.jti == claims.jti

    def test_expired_token(self, settings):
        past = datetime.now(UTC) - timedelta(hours=2)
        token = jwt.encode(
            {"sub": "1", "role": "CUSTOMER", "jti": "a", "iat": past, "exp": past + timedelta(minutes=1),
             "iss": settings.jwt_issuer, "typ": "access"},
            settings.jwt_secret, algorithm="HS256",
        )
        with pytest.raises(AuthenticationError) as exc:
            security.decode_access_token(settings, token)
        assert exc.value.code == "TOKEN_EXPIRED"

    def test_tampered_or_foreign_token(self, settings):
        token, _ = security.create_access_token(settings, 1, "CUSTOMER")
        with pytest.raises(AuthenticationError):
            security.decode_access_token(settings, token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1])
        forged = jwt.encode({"sub": "1", "role": "ADMIN", "jti": "x", "exp": int(time.time()) + 60,
                             "iss": settings.jwt_issuer, "typ": "access"}, "another-secret", algorithm="HS256")
        with pytest.raises(AuthenticationError):
            security.decode_access_token(settings, forged)

    def test_alg_none_rejected(self, settings):
        unsigned = jwt.encode({"sub": "1", "role": "ADMIN", "jti": "x", "exp": int(time.time()) + 60,
                               "iss": settings.jwt_issuer, "typ": "access"}, key=None, algorithm="none")
        with pytest.raises(AuthenticationError):
            security.decode_access_token(settings, unsigned)


# ------------------------------------------------------------------ pricing
def test_pricing_rules(settings):
    assert money("10.005") == money("10.01")
    assert shipping_fee(money("20"), settings) == money(settings.shipping_flat_fee)
    assert shipping_fee(money("150"), settings) == money(0)
    assert shipping_fee(money("0"), settings) == money(0)


# ------------------------------------------------------------------ cache
class TestCache:
    def test_memory_cache_ttl_and_counters(self):
        cache = CacheService(MemoryCache(), "t")
        cache.set_json("k", {"a": 1}, ttl=1)
        assert cache.get_json("k") == {"a": 1}
        assert cache.incr("c", 60) == 1 and cache.incr("c", 60) == 2
        cache.touch_active_user(1)
        cache.touch_active_user(2)
        cache.touch_active_user(1)
        assert cache.active_user_count() == 2
        v1 = cache.catalog_version()
        cache.bump_catalog_version()
        assert cache.catalog_version() != v1

    def test_redis_outage_degrades_gracefully(self):
        """DCS down: reads are misses, writes are skipped, nothing raises."""
        broken = CacheService(RedisCache(Settings(redis_host="127.0.0.1", redis_port=1, redis_socket_timeout=0.2)), "t")
        assert broken.get_json("x") is None
        broken.set_json("x", {"a": 1}, 10)
        assert broken.incr("x", 10) is None
        assert broken.active_user_count() == 0
        with pytest.raises(redis.RedisError):
            broken.ping()


# ------------------------------------------------------------------ storage
class TestStorage:
    def test_object_key_layout(self):
        assert product_image_key(5, "a.webp") == "images/products/5/a.webp"
        assert invoice_key(2026, 9, "INV-202609-000001") == "invoices/2026/09/INV-202609-000001.pdf"

    @pytest.mark.parametrize("bad", ["../etc/passwd", "/abs", "a//b", "images/../../x", "", "a b"])
    def test_invalid_keys_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_key(bad)

    def test_local_adapter_roundtrip_and_signed_urls(self, storage: LocalStorageService):
        obj = storage.upload("invoices/2026/09/INV-1.pdf", b"%PDF-1.4 test", "application/pdf")
        assert obj.size == 13
        assert storage.download("invoices/2026/09/INV-1.pdf") == b"%PDF-1.4 test"
        url = storage.generate_signed_url("invoices/2026/09/INV-1.pdf", 60)
        assert url.startswith("http://testserver/api/files/invoices/2026/09/INV-1.pdf?expires=")
        expires = int(url.split("expires=")[1].split("&")[0])
        sig = url.split("signature=")[1]
        assert storage.verify("invoices/2026/09/INV-1.pdf", expires, sig)
        assert not storage.verify("invoices/2026/09/INV-2.pdf", expires, sig)
        assert not storage.verify("invoices/2026/09/INV-1.pdf", int(time.time()) - 1, storage.sign("invoices/2026/09/INV-1.pdf", int(time.time()) - 1))
        storage.delete("invoices/2026/09/INV-1.pdf")
        with pytest.raises(ObjectNotFoundError):
            storage.download("invoices/2026/09/INV-1.pdf")

    def test_s3_adapter_routes_keys_to_buckets_and_presigns(self):
        s = Settings(
            storage_backend="s3", obs_endpoint="http://obs-internal:9000", obs_public_endpoint="https://obs.example.com",
            obs_bucket="default-bucket", obs_bucket_images="ecommerce-images", obs_bucket_invoices="ecommerce-invoices",
            obs_access_key="AK", obs_secret_key="SK", obs_addressing_style="path",
        )
        adapter = S3StorageService(s)
        assert adapter.bucket_for("images/products/1/a.webp") == "ecommerce-images"
        assert adapter.bucket_for("invoices/2026/09/x.pdf") == "ecommerce-invoices"
        assert adapter.bucket_for("other/thing") == "default-bucket"
        url = adapter.generate_signed_url("invoices/2026/09/x.pdf", 300, download_name="x.pdf")
        assert url.startswith("https://obs.example.com/ecommerce-invoices/invoices/2026/09/x.pdf?")
        assert "X-Amz-Signature=" in url and "X-Amz-Expires=300" in url

    def test_s3_public_url_for_images_only(self):
        s = Settings(storage_backend="s3", obs_endpoint="http://x", obs_bucket="b", obs_access_key="a", obs_secret_key="b",
                     obs_images_public_base_url="https://cdn.example.com/")
        adapter = S3StorageService(s)
        assert adapter.url_for("images/products/1/a.webp") == "https://cdn.example.com/images/products/1/a.webp"
        assert adapter.public_url("invoices/2026/01/a.pdf") is None


# ------------------------------------------------------------------ middleware
def _probe_app(trusted: str) -> TestClient:
    app = FastAPI()

    @app.get("/probe")
    def probe(request: Request) -> dict:
        return {"client": request.client.host, "scheme": request.url.scheme, "host": request.headers.get("host")}

    app.add_middleware(RequestContextMiddleware, service_name="t")
    app.add_middleware(ProxyHeadersMiddleware, trusted=Settings(trusted_proxies=trusted).trusted_proxy_networks)
    return TestClient(app)


class TestForwardedHeaders:
    HEADERS = {"X-Forwarded-For": "203.0.113.7, 10.0.2.15", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "shop.example.com"}

    def test_trusted_proxy_headers_applied(self):
        # TestClient's peer is "testclient" (not an IP) -> emulate trust via wildcard
        body = _probe_app("*").get("/probe", headers=self.HEADERS).json()
        assert body == {"client": "203.0.113.7", "scheme": "https", "host": "shop.example.com"}

    def test_untrusted_peer_headers_ignored(self):
        body = _probe_app("10.0.0.0/8").get("/probe", headers=self.HEADERS).json()
        assert body["scheme"] == "http" and body["client"] == "testclient" and body["host"] == "testserver"

    def test_client_cannot_spoof_leftmost_xff(self):
        """Attacker sends X-Forwarded-For: 1.1.1.1; the ELB appends the real IP; the ingress (trusted) forwards."""
        import asyncio

        seen = {}

        async def app(scope, receive, send):
            seen["client"] = scope["client"][0]

        mw = ProxyHeadersMiddleware(app, Settings(trusted_proxies="10.0.0.0/8").trusted_proxy_networks)
        scope = {"type": "http", "client": ("10.0.2.20", 5000),
                 "headers": [(b"x-forwarded-for", b"1.1.1.1, 203.0.113.9, 10.0.1.5")]}
        asyncio.run(mw(scope, None, None))
        assert seen["client"] == "203.0.113.9"

    def test_request_id_propagated_and_generated(self):
        c = _probe_app("*")
        assert c.get("/probe", headers={"X-Request-ID": "abcDEF123456"}).headers["x-request-id"] == "abcDEF123456"
        generated = c.get("/probe", headers={"X-Request-ID": "bad id with spaces"}).headers["x-request-id"]
        assert len(generated) == 32 and " " not in generated


# ------------------------------------------------------------------ logging
def test_json_logs_mask_secrets():
    record = logging.makeLogRecord({"name": "t", "levelno": 20, "levelname": "INFO", "msg": "hello",
                                    "password": "hunter2", "payload": {"card_number": "4242", "ok": 1}})
    out = json.loads(JsonFormatter().format(record))
    assert out["password"] == "***" and out["payload"] == {"card_number": "***", "ok": 1}
    assert mask_sensitive({"Authorization": "Bearer x"}) == {"Authorization": "***"}
