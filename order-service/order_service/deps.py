from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from ecommerce_common.config import Settings
from ecommerce_common.errors import AuthenticationError, PermissionDeniedError
from ecommerce_common.models import RoleName
from ecommerce_common.security import constant_time_equals

from .services.orders import OrderService


@dataclass(frozen=True)
class Caller:
    user_id: int | None
    role: str | None

    @property
    def is_admin(self) -> bool:
        return self.role == RoleName.ADMIN


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.db.session()
    try:
        yield session
    finally:
        session.close()


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


def get_caller(
    request: Request,
    x_internal_token: str | None = Header(default=None),
    x_user_id: int | None = Header(default=None),
    x_user_role: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Caller:
    """Service-to-service authentication: only the Backend API knows the shared token."""
    if not constant_time_equals(x_internal_token, settings.internal_service_token):
        raise AuthenticationError("Invalid service credentials", code="INVALID_SERVICE_TOKEN")
    if x_user_id is not None:
        request.state.user_id = x_user_id
    return Caller(user_id=x_user_id, role=x_user_role)


def require_user(caller: Caller = Depends(get_caller)) -> Caller:
    if caller.user_id is None:
        raise AuthenticationError("User context missing", code="UNAUTHENTICATED")
    return caller


def require_admin(caller: Caller = Depends(require_user)) -> Caller:
    if not caller.is_admin:
        raise PermissionDeniedError("Administrator role required")
    return caller
