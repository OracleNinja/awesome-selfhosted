"""The application's error vocabulary.

Why a hierarchy instead of raising ``HTTPException`` everywhere
---------------------------------------------------------------
``HTTPException`` is a transport concern. If the detection engine raises it,
the detection engine can only ever be called from HTTP — not from a worker, a
CLI command, or a test. So domain code raises :class:`AppError` subclasses that
describe *what went wrong*, and exactly one adapter (the API layer) decides how
that maps onto a status code and a JSON body.

Two rules this module enforces
------------------------------
1. **Every client-visible error has a stable machine code.** ``NOT_FOUND``,
   ``PERMISSION_DENIED``, ``VALIDATION_FAILED``. Clients branch on the code;
   the human-readable message is free to change wording without breaking them.
2. **Internal exceptions never reach the client.** Anything that is not an
   :class:`AppError` becomes a generic ``INTERNAL_ERROR`` carrying only a
   correlation id. Stack traces, SQL fragments, and file paths are attacker
   reconnaissance — they go to the log, which the operator can read, not to the
   response, which anyone can read.
"""

from __future__ import annotations

from typing import Any

# HTTP status codes are spelled out rather than imported from ``http`` so the
# mapping between domain error and wire status is visible in one place.
HTTP_400_BAD_REQUEST = 400
HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403
HTTP_404_NOT_FOUND = 404
HTTP_409_CONFLICT = 409
HTTP_413_PAYLOAD_TOO_LARGE = 413
HTTP_422_UNPROCESSABLE_ENTITY = 422
HTTP_429_TOO_MANY_REQUESTS = 429
HTTP_500_INTERNAL_SERVER_ERROR = 500
HTTP_503_SERVICE_UNAVAILABLE = 503


class AppError(Exception):
    """Base class for every expected failure.

    ``details`` must contain only data that is safe for the requesting client
    to see: which field failed validation, which permission was required. It is
    serialised verbatim into the response body, so never put a database error
    string or a filesystem path in it.
    """

    code = "INTERNAL_ERROR"
    status_code = HTTP_500_INTERNAL_SERVER_ERROR
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        if code:
            self.code = code
        super().__init__(self.message)

    def to_envelope(self, request_id: str | None = None) -> dict[str, Any]:
        """Render the standard error body.

        Every error response in NEXUS has this shape, including validation
        failures produced by the framework. A client can therefore write one
        error handler instead of three.
        """
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        if request_id:
            error["request_id"] = request_id
        return {"error": error}


class ValidationFailed(AppError):
    """Input did not satisfy the schema or a domain rule."""

    code = "VALIDATION_FAILED"
    status_code = HTTP_422_UNPROCESSABLE_ENTITY
    message = "The request payload failed validation."


class BadRequest(AppError):
    """Structurally malformed request — unparseable body, bad parameter type."""

    code = "BAD_REQUEST"
    status_code = HTTP_400_BAD_REQUEST
    message = "The request could not be understood."


class PayloadTooLarge(AppError):
    code = "PAYLOAD_TOO_LARGE"
    status_code = HTTP_413_PAYLOAD_TOO_LARGE
    message = "The request body is larger than the configured limit."


class Unauthenticated(AppError):
    """No valid session. Deliberately vague — see :mod:`nexus.core.security`.

    The message never distinguishes "unknown user" from "wrong password" from
    "expired session", because that distinction is a free account-enumeration
    oracle for anyone probing the login endpoint.
    """

    code = "UNAUTHENTICATED"
    status_code = HTTP_401_UNAUTHORIZED
    message = "Authentication is required."


class PermissionDenied(AppError):
    """Authenticated, but the role lacks the required permission."""

    code = "PERMISSION_DENIED"
    status_code = HTTP_403_FORBIDDEN
    message = "You do not have permission to perform this action."


class CSRFError(AppError):
    """State-changing request arrived without a valid anti-CSRF token."""

    code = "CSRF_FAILED"
    status_code = HTTP_403_FORBIDDEN
    message = "CSRF validation failed. Refresh the page and try again."


class NotFound(AppError):
    code = "NOT_FOUND"
    status_code = HTTP_404_NOT_FOUND
    message = "The requested resource does not exist."


class Conflict(AppError):
    """The request contradicts current state (duplicate name, stale version)."""

    code = "CONFLICT"
    status_code = HTTP_409_CONFLICT
    message = "The request conflicts with the current state of the resource."


class RateLimited(AppError):
    code = "RATE_LIMITED"
    status_code = HTTP_429_TOO_MANY_REQUESTS
    message = "Too many requests. Slow down and try again shortly."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after_seconds = retry_after_seconds


class NotConfigured(AppError):
    """A feature was invoked that the operator has not set up.

    This is a first-class error rather than a silent no-op because of the
    project's honesty rule: an unconfigured threat-intel provider must surface
    as NOT CONFIGURED in the UI, never as an empty result that reads like
    "nothing malicious found".
    """

    code = "NOT_CONFIGURED"
    status_code = HTTP_503_SERVICE_UNAVAILABLE
    message = "This capability is not configured on this deployment."


class DependencyUnavailable(AppError):
    """A dependency NEXUS relies on is down (database, sensor, upstream API).

    Distinct from :class:`NotConfigured`: the operator did set this up, and it
    is currently failing. The UI shows those two states differently because the
    remedy is different — configure it versus fix it.
    """

    code = "DEPENDENCY_UNAVAILABLE"
    status_code = HTTP_503_SERVICE_UNAVAILABLE
    message = "A required dependency is currently unavailable."


class OperationNotPermitted(AppError):
    """A safety invariant refused the operation.

    Used by the quarantine subsystem when a target is outside the operator's
    declared networks. Separate from :class:`PermissionDenied` (which is about
    *who* is asking) because this is about *what* is being asked: no role, not
    even ADMIN, can lift it.
    """

    code = "OPERATION_NOT_PERMITTED"
    status_code = HTTP_403_FORBIDDEN
    message = "This operation is not permitted by the platform's safety rules."
