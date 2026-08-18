"""Translation of exceptions into the one error format clients see.

There is exactly one JSON error shape in NEXUS::

    {"error": {"code": "...", "message": "...", "details": {...},
               "request_id": "..."}}

Framework validation failures, domain errors, and unhandled crashes are all
funnelled into it here. A client that handles one shape handles every failure.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from nexus.core.errors import (
    HTTP_500_INTERNAL_SERVER_ERROR,
    AppError,
    RateLimited,
    ValidationFailed,
)
from nexus.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

# Status codes that Starlette raises internally, mapped to our vocabulary so a
# missing route and a missing record look the same to a client parser.
_HTTP_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "DEPENDENCY_UNAVAILABLE",
}


def _envelope_response(error: AppError, *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_envelope(request_id_var.get()),
        headers=headers,
    )


async def app_error_handler(request: Request, exc: Exception) -> Response:
    """Domain errors: already client-safe by construction."""
    assert isinstance(exc, AppError)
    headers: dict[str, str] | None = None
    if isinstance(exc, RateLimited) and exc.retry_after_seconds is not None:
        # Tells a well-behaved client exactly how long to wait instead of
        # leaving it to guess and retry into the same limit.
        headers = {"Retry-After": str(exc.retry_after_seconds)}

    # 5xx means *we* failed; 4xx means the caller did. Logging both at the same
    # level makes the log useless for alerting.
    if exc.status_code >= HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error(
            "app_error",
            extra={"code": exc.code, "path": request.url.path, "status": exc.status_code},
        )
    else:
        logger.info(
            "app_error",
            extra={"code": exc.code, "path": request.url.path, "status": exc.status_code},
        )
    return _envelope_response(exc, headers=headers)


async def validation_error_handler(request: Request, exc: Exception) -> Response:
    """Schema validation failures from FastAPI/pydantic.

    Pydantic's raw error list contains an ``input`` key holding the offending
    value. For a login request that value is the password. It is stripped here
    — the client already knows what it sent, and the server must not echo
    credentials into a response body or a log line.
    """
    assert isinstance(exc, RequestValidationError)
    fields: list[dict[str, Any]] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        fields.append(
            {
                "field": location,
                "message": str(item.get("msg", "invalid value")),
                "type": str(item.get("type", "value_error")),
            }
        )
    error = ValidationFailed(details={"fields": fields})
    logger.info(
        "request_validation_failed",
        extra={"path": request.url.path, "field_count": len(fields)},
    )
    return _envelope_response(error)


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    """Starlette's own HTTPException (unknown route, wrong method)."""
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_STATUS_CODES.get(exc.status_code, "ERROR")
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    error = AppError(detail, code=code)
    error.status_code = exc.status_code
    headers = dict(exc.headers) if exc.headers else None
    return _envelope_response(error, headers=headers)


async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    """Last resort. The client learns nothing except a correlation id.

    This is the boundary between "what the operator can see in the log" and
    "what an anonymous caller can see in a response". Everything useful — type,
    message, traceback — goes left; only the request id goes right. Combined
    with the ``X-Request-ID`` response header, an operator can still tie a user
    report to the exact stack trace.
    """
    request_id = request_id_var.get()
    logger.exception(
        "unhandled_exception",
        extra={
            "path": request.url.path,
            "method": request.method,
            "exception_type": exc.__class__.__name__,
        },
    )
    error = AppError("An internal error occurred. Quote the request id when reporting this.")
    return JSONResponse(
        status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        content=error.to_envelope(request_id),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the handlers. Order is irrelevant; specificity wins in Starlette."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
