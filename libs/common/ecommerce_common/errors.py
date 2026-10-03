"""Uniform API error model and FastAPI exception handlers.

Every error response has the shape::

    {"error": {"code": "INSUFFICIENT_INVENTORY", "message": "...", "details": ..., "request_id": "..."}}

Stack traces are logged server-side only and never returned to clients.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .log import request_id_var

logger = logging.getLogger("ecommerce.errors")


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class AuthenticationError(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details is not None:
        body["details"] = details
    return {"error": body}


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.warning("request failed", extra={"error_code": exc.code, "error": exc.message})
        headers = {"Retry-After": "60"} if exc.status_code == 429 else None
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Echoing the raw input back could leak passwords, so only location + message.
        details = [
            {"field": ".".join(str(p) for p in err.get("loc", []) if p != "body"), "message": err.get("msg")}
            for err in exc.errors()
        ]
        return JSONResponse(
            error_body("VALIDATION_ERROR", "Request validation failed", details), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").title()
        return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def _db_unavailable(_: Request, exc: Exception) -> JSONResponse:
        logger.error("database unavailable", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            error_body("DATABASE_UNAVAILABLE", "The database is temporarily unavailable, please retry"),
            status_code=503,
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(DBAPIError)
    async def _db_error(_: Request, exc: DBAPIError) -> JSONResponse:
        if exc.connection_invalidated:
            return await _db_unavailable(_, exc)
        logger.exception("database error")
        return JSONResponse(error_body("INTERNAL_ERROR", "An unexpected error occurred"), status_code=500)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception", exc_info=exc)
        return JSONResponse(error_body("INTERNAL_ERROR", "An unexpected error occurred"), status_code=500)
