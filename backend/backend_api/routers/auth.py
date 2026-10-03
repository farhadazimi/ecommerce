from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ecommerce_common.cache import CacheService
from ecommerce_common.config import Settings
from ecommerce_common.errors import AuthenticationError, ConflictError
from ecommerce_common.log import log_event
from ecommerce_common.metrics import LOGINS, USERS_REGISTERED
from ecommerce_common.models import Role, RoleName, User, UserProfile, utcnow
from ecommerce_common.security import (
    TokenClaims,
    create_access_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)
from ecommerce_common.storage import StorageService

from ..deps import (
    auth_rate_limit,
    client_ip,
    get_cache,
    get_current_user,
    get_db,
    get_settings,
    get_storage,
    get_token_claims,
    revoked_key,
)
from ..schemas import AuthResponse, LoginRequest, Message, RegisterRequest, UserOut
from ..services.serializers import user_out

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
logger = logging.getLogger("ecommerce.auth")


def _issue(response: Response, settings: Settings, user: User, storage: StorageService) -> AuthResponse:
    token, claims = create_access_token(settings, user.id, user.role_name)
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=settings.jwt_expires_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    return AuthResponse(access_token=token, expires_at=claims.expires_at, user=user_out(user, storage))


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit)],
    summary="Register a customer account (also logs the user in)",
)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> AuthResponse:
    email = body.email.lower()
    if db.scalar(select(func.count()).select_from(User).where(User.email == email)):
        raise ConflictError("An account with this e-mail already exists", code="EMAIL_TAKEN")
    role = db.scalar(select(Role).where(Role.name == RoleName.CUSTOMER))
    if role is None:  # pragma: no cover - seeded by migrations
        raise RuntimeError("CUSTOMER role missing; run database migrations")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name, role=role)
    user.profile = UserProfile()
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("An account with this e-mail already exists", code="EMAIL_TAKEN") from exc
    db.refresh(user)
    request.state.user_id = user.id
    USERS_REGISTERED.inc()
    log_event("USER_REGISTERED", user_id=user.id, client_ip=client_ip(request))
    return _issue(response, settings, user, storage)


@router.post("/login", response_model=AuthResponse, dependencies=[Depends(auth_rate_limit)], summary="Log in")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # always run bcrypt so response time does not reveal whether the e-mail exists
    valid = verify_password(body.password, user.password_hash if user else dummy_password_hash())
    if not user or not valid:
        LOGINS.labels("failure").inc()
        log_event("USER_LOGIN_FAILED", client_ip=client_ip(request), reason="invalid_credentials")
        raise AuthenticationError("Invalid e-mail or password", code="INVALID_CREDENTIALS")
    if not user.is_active:
        LOGINS.labels("failure").inc()
        log_event("USER_LOGIN_FAILED", user_id=user.id, client_ip=client_ip(request), reason="inactive")
        raise AuthenticationError("Account is disabled", code="ACCOUNT_INACTIVE")
    user.last_login_at = utcnow()
    db.commit()
    request.state.user_id = user.id
    LOGINS.labels("success").inc()
    log_event("USER_LOGIN", user_id=user.id, role=user.role_name, client_ip=client_ip(request))
    return _issue(response, settings, user, storage)


@router.post("/logout", response_model=Message, summary="Log out (revokes the current token)")
def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    cache: CacheService = Depends(get_cache),
) -> Message:
    try:
        claims: TokenClaims | None = get_token_claims(request, settings, cache)
    except AuthenticationError:
        claims = None
    if claims:
        ttl = max(int(claims.expires_at.timestamp() - time.time()), 1)
        cache.set(revoked_key(cache, claims.jti), "1", ttl)  # deny-list until natural expiry
        log_event("USER_LOGOUT", user_id=claims.user_id)
    response.delete_cookie(settings.auth_cookie_name, path="/", domain=settings.cookie_domain)
    return Message(message="Logged out")


@router.get("/me", response_model=UserOut, summary="Current user")
def me(user: User = Depends(get_current_user), storage: StorageService = Depends(get_storage)) -> UserOut:
    return user_out(user, storage)
