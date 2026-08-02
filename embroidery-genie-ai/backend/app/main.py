"""Embroidery Genie AI — FastAPI application."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.api import api_router
from app.core.config import settings
from app.embroidery.text import find_font
from app.embroidery.writers import HAS_PYEMBROIDERY, available_formats

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("embroidery_genie")


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = settings.validate_runtime()
    if problems:
        for problem in problems:
            log.error("Configuration error: %s", problem)
        if settings.is_production:
            raise RuntimeError(
                "Refusing to start with an unsafe production configuration: "
                + "; ".join(problems)
            )

    log.info("%s v%s starting in %s mode", settings.app_name, __version__, settings.environment)
    log.info("Storage backend: %s", settings.storage_backend)
    log.info(
        "Export formats: %s",
        ", ".join(f["extension"] for f in available_formats() if f["available"]),
    )
    if not HAS_PYEMBROIDERY:
        log.warning(
            "pyembroidery is not installed — only DST and EXP export are available."
        )
    try:
        find_font()
    except FileNotFoundError:
        log.warning(
            "No TrueType font found; text designs will fail. "
            "Install fonts-dejavu-core."
        )
    if not settings.ai_enabled():
        log.info("No AI provider configured — analysis runs on computer vision only.")

    yield
    log.info("Shutting down.")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "AI-powered embroidery digitizing and production management.\n\n"
        "Upload artwork, get machine-ready stitch files, thread charts, "
        "production paperwork and pricing."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Export-Warnings", "X-Request-Id"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a request id and log slow requests.

    Digitizing is CPU-bound and can legitimately take seconds; knowing which
    request that was is the difference between tuning and guessing.
    """
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    # Stash on state so handlers and the exception handlers can log the same id
    # the client sees in the response header.
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - start) * 1000
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed:.0f}"
    if elapsed > 3000:
        log.warning("Slow request %s %s took %.0f ms [%s]",
                    request.method, request.url.path, elapsed, request_id)
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """Readable validation errors — the raw pydantic shape is not user-facing."""
    problems = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        problems.append({"field": location or "request", "message": error["msg"]})
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": problems[0]["message"] if problems else "Invalid request.",
            "problems": problems,
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "-")
    log.exception(
        "Unhandled error on %s %s [%s]", request.method, request.url.path, request_id
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                f"Something went wrong on our side. Quote reference {request_id} "
                "if you contact support."
                if settings.is_production
                else f"{type(exc).__name__}: {exc}"
            ),
            "request_id": request_id,
        },
    )


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "version": __version__, "environment": settings.environment}


@app.get("/ready", tags=["system"])
def ready() -> JSONResponse:
    """Readiness probe: verifies the database is reachable."""
    from sqlalchemy import text

    from app.db.session import engine

    checks = {"database": False}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:  # pragma: no cover - depends on the environment
        log.error("Readiness check failed: %s", exc)

    healthy = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "api": settings.api_v1_prefix,
    }


app.include_router(api_router, prefix=settings.api_v1_prefix)
