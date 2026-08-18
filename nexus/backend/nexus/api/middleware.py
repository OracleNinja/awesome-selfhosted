"""ASGI middleware: correlation, access logging, body limits, security headers.

Middleware is the layer that wraps *every* request, so it is where
cross-cutting guarantees belong — the ones that must hold even for a route
someone adds next year without reading this file. Order matters and is set in
:mod:`nexus.app`; each class below documents where it must sit.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexus.core.config import Settings
from nexus.core.errors import PayloadTooLarge
from nexus.core.logging import bind_request_id, get_logger, request_id_var, user_id_var

logger = get_logger("nexus.access")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware:
    """Assigns a request id, logs the request, and measures its duration.

    Must be the outermost middleware so that everything else — including errors
    raised by other middleware — is logged with a correlation id.

    An inbound ``X-Request-ID`` is honoured only when it looks like an id we
    would have generated. Echoing arbitrary client input into every log line is
    a log-injection vector: a client could send a newline and forge log entries.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = bind_request_id(_sanitise_request_id(headers.get(REQUEST_ID_HEADER)))
        user_id_var.set(None)

        started = time.perf_counter()
        status_holder: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                # Returned on every response, including errors, so an operator
                # can paste the id from a browser's network tab into a log query.
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request_failed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "duration_ms": duration_ms,
                },
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            status = status_holder.get("status", 0)
            # Health probes fire every few seconds; logging them at INFO buries
            # everything else. They stay available at DEBUG.
            level = "debug" if _is_probe(scope.get("path", "")) else "info"
            getattr(logger, level)(
                "request_completed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status,
                    "duration_ms": duration_ms,
                    "user_id": user_id_var.get(),
                },
            )


def _sanitise_request_id(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if 8 <= len(candidate) <= 64 and all(char.isalnum() or char in "-_" for char in candidate):
        return candidate
    return None


def _is_probe(path: str) -> bool:
    return path.endswith(("/health", "/health/live", "/health/ready"))


class BodySizeLimitMiddleware:
    """Rejects oversized request bodies before they are buffered in memory.

    Two checks, because either alone is insufficient:

    * ``Content-Length`` is checked up front — cheap, and stops the common case
      before a single byte of body is read.
    * Streamed chunks are counted as they arrive, because a chunked request has
      no ``Content-Length`` and could otherwise stream gigabytes into memory.

    Without this, one client can exhaust the process's memory with a single
    request — a denial of service that needs no botnet and no exploit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                # A malformed Content-Length is a malformed request; let the
                # server's own parser reject it rather than guessing.
                pass

        received = 0
        exceeded = False

        async def receive_wrapper() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # Truncate the stream so the handler sees a short body and
                    # stops, rather than continuing to consume the socket.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        await self.app(scope, receive_wrapper, send)
        if exceeded:  # pragma: no cover - defensive: response already sent
            logger.warning(
                "request_body_limit_exceeded",
                extra={"path": scope.get("path"), "limit_bytes": self.max_bytes},
            )

    async def _reject(self, send: Send) -> None:
        error = PayloadTooLarge(details={"limit_bytes": self.max_bytes})
        response = JSONResponse(
            status_code=error.status_code,
            content=error.to_envelope(request_id_var.get()),
        )
        # Called as a raw ASGI app rather than returned, because middleware at
        # this level has no response pipeline to return into — it must speak
        # ASGI directly.
        await response({"type": "http"}, _empty_receive, send)


async def _empty_receive() -> Message:  # pragma: no cover - never awaited
    return {"type": "http.disconnect"}


class SecurityHeadersMiddleware:
    """Adds the response headers that constrain what a browser will do.

    Each header exists to remove a class of attack:

    * ``Content-Security-Policy`` — the main defence in depth against XSS. Even
      if markup injection succeeds, the browser refuses to execute injected
      script because it is not from an allowed source. NEXUS ships no inline
      scripts, so ``'self'`` needs no unsafe-inline escape hatch.
    * ``X-Content-Type-Options: nosniff`` — stops a browser from deciding that a
      JSON response is really HTML and rendering it, which turns a reflected
      value into stored XSS.
    * ``X-Frame-Options: DENY`` — clickjacking: no framing means no invisible
      overlay tricking an operator into clicking "quarantine".
    * ``Referrer-Policy`` — keeps device ids and query filters in URLs from
      leaking to any third party the operator navigates to.
    * ``Permissions-Policy`` — this app needs no camera, microphone, or
      geolocation, so it declines them all.
    * ``Strict-Transport-Security`` — production only, and only meaningful over
      HTTPS; sending it over plain HTTP would pin a browser to a scheme the
      deployment may not serve.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._headers = self._build_headers(settings)

    @staticmethod
    def _build_headers(settings: Settings) -> list[tuple[bytes, bytes]]:
        csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            # The SPA ships styles in a stylesheet, but component libraries set
            # inline style attributes for dynamic values (chart geometry), which
            # style-src 'unsafe-inline' covers. It does not permit script.
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            # SSE and fetch to our own origin only.
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'"
        )
        headers = [
            (b"content-security-policy", csp.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (
                b"permissions-policy",
                b"camera=(), microphone=(), geolocation=(), interest-cohort=()",
            ),
            (b"cross-origin-opener-policy", b"same-origin"),
        ]
        if settings.environment.is_production:
            headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self._headers:
                    headers.setdefault(key.decode(), value.decode())
            await send(message)

        await self.app(scope, receive, send_wrapper)


def client_ip(request: Request, settings: Settings) -> str | None:
    """Resolve the client address, honouring proxies only when configured.

    ``X-Forwarded-For`` is a client-supplied header. If NEXUS trusted it
    unconditionally, anyone could write whatever source address they liked into
    the audit log — which is precisely the record you consult after an
    incident. So it is used only when the operator has declared how many
    trusted proxies sit in front, and the address is taken from the right end of
    the chain: with N trusted hops, the N-th entry from the right is the last
    address a trusted proxy observed.
    """
    if settings.trusted_proxy_hops > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            index = len(parts) - settings.trusted_proxy_hops
            if 0 <= index < len(parts):
                return parts[index]
    return request.client.host if request.client else None


ExceptionHandler = Callable[[Request, Exception], Awaitable[Response]]
