"""Application error types and a uniform, safe JSON error envelope."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.logging import request_id_var

log = logging.getLogger(__name__)


class AppError(Exception):
    """An error whose message is safe to show to the client."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(AppError):
    status_code, code = 404, "not_found"


class ConflictError(AppError):
    status_code, code = 409, "conflict"


class AuthError(AppError):
    status_code, code = 401, "unauthorized"


class ProviderNotConfiguredError(AppError):
    status_code, code = 409, "provider_not_configured"


class ModelValidationError(AppError):
    status_code, code = 422, "invalid_model"


def _envelope(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid[:64])
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid[:64]
        return response

    from slowapi.errors import RateLimitExceeded

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        resp = _envelope(429, "rate_limited", "Too many requests. Please try again shortly.")
        resp.headers["Retry-After"] = "60"
        return resp

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return _envelope(422, "validation_error", "Request validation failed.", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _envelope(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error: %s", type(exc).__name__)
        return _envelope(500, "internal_error", "An internal error occurred.")
