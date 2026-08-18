"""Health and readiness endpoints.

Three endpoints, because "is it healthy" is three different questions asked by
three different callers:

``/health/live``
    Is the process running? Answered without touching any dependency. An
    orchestrator restarts a container that fails this, so it must not fail
    because the *database* is down — restarting the app does not fix the
    database, it just adds an outage to an outage.

``/health/ready``
    Can it serve traffic? Checks dependencies. A load balancer stops sending
    requests to an unready instance, which is the correct response to a broken
    database: stop routing, do not restart.

``/health``
    What exactly is wrong? Component-by-component summary for humans and for
    the frontend's degraded-mode banner.

All three are unauthenticated by necessity: probes run before login exists and
often without credentials. So they expose *status*, never internals — no
connection strings, no versions of dependencies, no error text that names
internal hosts. The rich, authenticated view lives under ``/system/status``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from nexus import __version__
from nexus.api.deps import ContextDep
from nexus.core.errors import HTTP_503_SERVICE_UNAVAILABLE
from nexus.core.logging import scrub_text

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: str = Field(description="Always 'alive' when the process can answer.")
    uptime_seconds: float
    version: str


class ComponentStatus(BaseModel):
    name: str
    status: str = Field(
        description="ok | degraded | unavailable | not_configured",
    )
    detail: str | None = Field(
        default=None, description="Short, non-sensitive explanation when not ok."
    )


class HealthResponse(BaseModel):
    status: str = Field(description="Aggregate: ok | degraded | unavailable")
    ready: bool
    uptime_seconds: float
    version: str
    components: list[ComponentStatus]


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Returns 200 whenever the process is running. Never touches the "
        "database, so a database outage does not cause a restart loop."
    ),
)
async def liveness(context: ContextDep) -> LivenessResponse:
    return LivenessResponse(
        status="alive", uptime_seconds=context.uptime_seconds, version=__version__
    )


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    summary="Readiness probe",
    description=(
        "Returns 200 when every required dependency is usable, and 503 "
        "otherwise. Load balancers should drain instances that return 503."
    ),
    responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def readiness(context: ContextDep, response: Response) -> HealthResponse:
    status, components = await context.overall_status()
    ready = status == "ok"
    if not ready:
        # The body is still the normal shape — a probe that gets an error
        # envelope cannot tell you *which* component failed.
        response.status_code = HTTP_503_SERVICE_UNAVAILABLE
    return _build(context, status, ready, components)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Component health summary",
    description=(
        "Per-component status for dashboards and the frontend's degraded-mode "
        "banner. Always returns 200 so a monitoring tool can distinguish "
        "'NEXUS says something is wrong' from 'NEXUS is unreachable'."
    ),
)
async def health(context: ContextDep) -> HealthResponse:
    status, components = await context.overall_status()
    return _build(context, status, status == "ok", components)


def _build(context, status: str, ready: bool, components) -> HealthResponse:
    return HealthResponse(
        status=status,
        ready=ready,
        uptime_seconds=context.uptime_seconds,
        version=__version__,
        components=[
            ComponentStatus(
                name=item.name,
                status=item.status,
                # These endpoints are unauthenticated, and `detail` is a
                # pass-through channel for text produced deep inside a
                # subsystem — a driver message, an upstream API's response.
                # Scrubbing here means a future component cannot leak a
                # credential to an anonymous caller by being careless with its
                # own error strings.
                detail=scrub_text(item.detail) if item.detail else None,
            )
            for item in components
        ],
    )
