"""Application factory.

``create_app`` builds a fully wired FastAPI application from a
:class:`~nexus.core.config.Settings` object. Nothing is constructed at import
time, which is what makes the following possible:

* a test builds an app against a scratch database in three lines;
* a worker process imports the domain services without starting an HTTP server;
* configuration errors surface when someone *asks* for an app, not as an
  import-time side effect that a linter or a doc generator can trigger.

Middleware order
----------------
Starlette applies middleware outermost-first in the reverse of the order they
are added, so the ``add_middleware`` calls below read bottom-up:

    RequestContext        (outermost — every request gets an id, even failures)
      SecurityHeaders     (headers added to every response, including errors)
        CORS              (must answer OPTIONS preflight before auth logic)
          BodySizeLimit   (innermost — rejects huge bodies before routing)
            routes
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nexus import __version__
from nexus.api.errors import register_exception_handlers
from nexus.api.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from nexus.api.v1.router import api_router
from nexus.core.config import Settings, get_settings
from nexus.core.context import AppContext
from nexus.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

API_V1_PREFIX = "/api/v1"

DESCRIPTION = """
NEXUS is a self-hosted network security monitoring and cybersecurity laboratory
platform.

**Authentication.** Session cookie, obtained from `POST /api/v1/auth/login`.
State-changing requests must also carry the `X-CSRF-Token` header returned by
that endpoint.

**Errors.** Every failure returns the same envelope:
`{"error": {"code", "message", "details?", "request_id?"}}`. Branch on `code`.

**Honesty.** Capabilities that are not configured on a deployment report
`NOT_CONFIGURED` rather than returning empty results that could be mistaken
for "nothing found".
""".strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    context = AppContext(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Start and stop long-lived resources around the server's life.

        Using lifespan rather than module-level setup means resources are tied
        to the server's actual runtime: they are released on shutdown even when
        the process is stopped by a signal, and a test client that exits its
        context manager leaves no connection pool behind.
        """
        logger.info(
            "starting",
            extra={"version": __version__, "config": settings.redacted_summary},
        )
        await context.startup()
        try:
            yield
        finally:
            # Runs even if startup partially failed, so a half-initialised
            # process still releases what it managed to acquire.
            await context.shutdown()
            logger.info("stopped")

    app = FastAPI(
        title="NEXUS",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        # Served under /api so a reverse proxy can route /api to the backend
        # and everything else to the frontend bundle with one prefix rule.
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        # Our own handlers own the error format; FastAPI's default
        # {"detail": ...} shape never reaches a client.
        responses={},
    )

    app.state.context = context
    app.state.settings = settings

    app.include_router(api_router, prefix=API_V1_PREFIX)
    register_exception_handlers(app)

    # --- innermost first (see module docstring for the resulting order) ---
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)

    if settings.cors_origins:
        # allow_credentials with an explicit origin list only. The browser
        # refuses "*" together with credentials, and configuration validation
        # already rejects "*" in production — belt and braces, because a
        # permissive CORS policy on a cookie-authenticated admin console is a
        # one-line path to cross-origin request forgery.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)

    return app
