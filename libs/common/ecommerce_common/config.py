"""Twelve-factor configuration.

All settings come from environment variables (Kubernetes ConfigMaps / Secrets in the
cloud, ``.env`` files locally). Nothing environment specific is hard-coded; the
defaults below are only safe for local development and are rejected in
staging/production by :meth:`Settings._validate_environment`.
"""

from __future__ import annotations

import ipaddress
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"
DEV_INTERNAL_TOKEN = "dev-only-internal-service-token"
PLACEHOLDER_MARKERS = ("CHANGE_ME", "change-me", "dev-only")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # ---- runtime -----------------------------------------------------------------
    app_env: Literal["development", "test", "staging", "production"] = "development"
    service_name: str = "ecommerce"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    log_json: bool = True

    # ---- RDS MySQL ---------------------------------------------------------------
    database_url: str | None = None  # full SQLAlchemy URL overrides the parts below
    database_host: str = "localhost"
    database_port: int = 3306
    database_name: str = "ecommerce"
    database_user: str = "ecommerce"
    database_password: str = ""
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_recycle: int = 1800
    database_pool_timeout: int = 10
    database_connect_timeout: int = 5
    database_ssl_ca: str | None = None  # path to the RDS CA bundle to enforce TLS

    # ---- DCS Redis ---------------------------------------------------------------
    redis_enabled: bool = True
    redis_required: bool = False  # when true, /ready fails if Redis is unreachable
    redis_host: str | None = None  # unset -> in-memory fallback (development only)
    redis_port: int = 6379
    redis_password: str | None = None
    redis_db: int = 0
    redis_ssl: bool = False
    redis_socket_timeout: float = 1.0
    cache_prefix: str = "ecom"
    catalog_cache_ttl: int = 120
    cart_cache_ttl: int = 300

    # ---- OBS object storage ------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: str = "/tmp/ecommerce-storage"  # noqa: S108 - development-only adapter
    obs_endpoint: str | None = None  # internal endpoint used by the pods
    obs_public_endpoint: str | None = None  # endpoint browsers use for signed URLs
    obs_bucket: str | None = None  # default bucket
    obs_bucket_images: str | None = None  # e.g. ecommerce-images (falls back to OBS_BUCKET)
    obs_bucket_invoices: str | None = None  # e.g. ecommerce-invoices (falls back to OBS_BUCKET)
    obs_access_key: str | None = None
    obs_secret_key: str | None = None
    obs_region: str = "us-east-1"
    obs_addressing_style: Literal["auto", "path", "virtual"] = "auto"
    obs_images_public_base_url: str | None = None  # public-read bucket / CDN base URL
    obs_signed_url_ttl: int = 3600
    max_upload_mb: int = 5

    # ---- security ----------------------------------------------------------------
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60
    jwt_issuer: str = "ecommerce-backend"
    auth_cookie_name: str = "ecom_session"
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    trusted_proxies: str = "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    internal_service_token: str = DEV_INTERNAL_TOKEN
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 300
    rate_limit_auth_per_minute: int = 20
    expose_api_docs: bool = True

    # ---- service discovery -------------------------------------------------------
    order_service_url: str = "http://localhost:8001"
    order_service_timeout: float = 10.0
    public_base_url: str = "http://localhost:8000"  # backend URL (local file URLs only)

    # ---- notifications (SMN) -----------------------------------------------------
    notification_backend: Literal["log", "webhook"] = "log"
    smn_webhook_url: str | None = None
    smn_webhook_token: str | None = None

    # ---- business rules ----------------------------------------------------------
    currency: str = "USD"
    shipping_flat_fee: Decimal = Decimal("5.00")
    free_shipping_threshold: Decimal = Decimal("100.00")
    order_payment_timeout_minutes: int = 30
    order_reaper_enabled: bool = True
    order_reaper_interval_seconds: int = 60

    # ---------------------------------------------------------------------------------
    @property
    def is_production_like(self) -> bool:
        return self.app_env in ("staging", "production")

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"mysql+pymysql://{quote_plus(self.database_user)}:{quote_plus(self.database_password)}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}?charset=utf8mb4"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        nets = []
        for item in self.trusted_proxies.split(","):
            item = item.strip()
            if not item:
                continue
            if item == "*":
                return [ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0")]
            nets.append(ipaddress.ip_network(item, strict=False))
        return nets

    @property
    def images_bucket(self) -> str | None:
        return self.obs_bucket_images or self.obs_bucket

    @property
    def invoices_bucket(self) -> str | None:
        return self.obs_bucket_invoices or self.obs_bucket

    @model_validator(mode="after")
    def _validate_environment(self) -> Settings:
        if not self.is_production_like:
            return self
        problems: list[str] = []

        def looks_placeholder(value: str | None) -> bool:
            return not value or any(m in value for m in PLACEHOLDER_MARKERS)

        if looks_placeholder(self.jwt_secret) or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be a real secret of at least 32 characters")
        if looks_placeholder(self.internal_service_token) or len(self.internal_service_token) < 24:
            problems.append("INTERNAL_SERVICE_TOKEN must be a real secret of at least 24 characters")
        if not self.database_url and looks_placeholder(self.database_password):
            problems.append("DATABASE_PASSWORD must be provided")
        if self.storage_backend != "s3":
            problems.append("STORAGE_BACKEND must be 's3' (OBS) outside development")
        if self.storage_backend == "s3" and (
            looks_placeholder(self.obs_access_key) or looks_placeholder(self.obs_secret_key)
        ):
            problems.append("OBS_ACCESS_KEY / OBS_SECRET_KEY must be provided")
        if self.redis_enabled and not self.redis_host:
            problems.append("REDIS_HOST must be set (DCS) outside development")
        if problems:
            raise ValueError("Insecure or incomplete configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
