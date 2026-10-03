"""Password hashing (bcrypt) and JWT access tokens."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import bcrypt
import jwt

from .config import Settings
from .errors import AuthenticationError

_BCRYPT_ROUNDS = 12


def hash_password(password: str, rounds: int | None = None) -> str:
    # bcrypt only uses the first 72 bytes; the API caps password length below that.
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds or _BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("ascii"))
    except ValueError:
        return False


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """Hash compared against when the e-mail does not exist, keeping login timing constant."""
    return hash_password(uuid.uuid4().hex)


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    role: str
    jti: str
    expires_at: datetime


def create_access_token(settings: Settings, user_id: int, role: str) -> tuple[str, TokenClaims]:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.jwt_expires_minutes)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": jti,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": settings.jwt_issuer,
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, TokenClaims(user_id=user_id, role=role, jti=jti, expires_at=expires)


def decode_access_token(settings: Settings, token: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "jti", "iss"]},
            leeway=10,
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired", code="TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid access token", code="INVALID_TOKEN") from exc
    if payload.get("typ") != "access":
        raise AuthenticationError("Invalid access token", code="INVALID_TOKEN")
    return TokenClaims(
        user_id=int(payload["sub"]),
        role=str(payload.get("role", "")),
        jti=str(payload["jti"]),
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
    )


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode(), b.encode())
