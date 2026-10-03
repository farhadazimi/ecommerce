"""FastAPI dependencies: settings, DB session, adapters, authentication, rate limiting."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ecommerce_common.cache import CacheService
from ecommerce_common.config import Settings
from ecommerce_common.errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from ecommerce_common.models import RoleName, User
from ecommerce_common.notifications import NotificationService
from ecommerce_common.security import TokenClaims, decode_access_token
from ecommerce_common.storage import StorageService

from .services.order_client import OrderServiceClient


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.db.session()
    try:
        yield session
    finally:
        session.close()


def get_cache(request: Request) -> CacheService:
    return request.app.state.cache


def get_storage(request: Request) -> StorageService:
    return request.app.state.storage


def get_notifier(request: Request) -> NotificationService:
    return request.app.state.notifier


def get_order_client(request: Request) -> OrderServiceClient:
    return request.app.state.order_client


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------------ authentication
def _extract_token(request: Request, settings: Settings) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.cookies.get(settings.auth_cookie_name)


def revoked_key(cache: CacheService, jti: str) -> str:
    return cache.key("revoked", jti)


def get_token_claims(
    request: Request,
    settings: Settings = Depends(get_settings),
    cache: CacheService = Depends(get_cache),
) -> TokenClaims:
    token = _extract_token(request, settings)
    if not token:
        raise AuthenticationError("Authentication required", code="UNAUTHENTICATED")
    claims = decode_access_token(settings, token)
    if cache.get(revoked_key(cache, claims.jti)):
        raise AuthenticationError("Session has been logged out", code="TOKEN_REVOKED")
    return claims


def get_current_user(
    request: Request,
    claims: TokenClaims = Depends(get_token_claims),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
) -> User:
    user = db.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is not active", code="ACCOUNT_INACTIVE")
    request.state.user_id = user.id  # picked up by the access log
    cache.touch_active_user(user.id)
    return user


def get_optional_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    cache: CacheService = Depends(get_cache),
    db: Session = Depends(get_db),
) -> User | None:
    token = _extract_token(request, settings)
    if not token:
        return None
    try:
        claims = get_token_claims(request, settings, cache)
    except AuthenticationError:
        return None
    user = db.get(User, claims.user_id)
    return user if user and user.is_active else None


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role_name != RoleName.ADMIN:
        raise PermissionDeniedError("Administrator role required")
    return user


# ------------------------------------------------------------------ rate limiting
def rate_limit(name: str, limit_attr: str) -> Callable[..., None]:
    """Fixed-window per-IP limiter stored in Redis (fails open if Redis is down)."""

    def dependency(
        request: Request,
        settings: Settings = Depends(get_settings),
        cache: CacheService = Depends(get_cache),
    ) -> None:
        if not settings.rate_limit_enabled:
            return
        limit = int(getattr(settings, limit_attr))
        window = int(time.time() // 60)
        count = cache.incr(cache.key("ratelimit", name, client_ip(request), window), ttl=61)
        if count is not None and count > limit:
            raise RateLimitedError("Too many requests, please slow down")

    return dependency


default_rate_limit = rate_limit("api", "rate_limit_default_per_minute")
auth_rate_limit = rate_limit("auth", "rate_limit_auth_per_minute")
