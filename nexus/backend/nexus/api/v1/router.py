"""Assembly point for version 1 of the API.

Routers are composed here rather than in the application factory so that
mounting a new resource is a one-line change in one file, and so the set of
endpoints in v1 is readable at a glance.

Versioning policy: ``/api/v1`` is a contract. Additive changes (a new endpoint,
a new optional field) ship inside v1; anything that could break a client —
removing a field, changing a type, tightening validation — waits for ``/api/v2``,
which would be a sibling of this module. See docs/API.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from nexus.api.v1.routes import (
    audit,
    auth,
    detections,
    devices,
    events,
    health,
    jobs,
    sensors,
    users,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(audit.router)
api_router.include_router(events.router)
api_router.include_router(devices.router)
api_router.include_router(detections.router)
api_router.include_router(sensors.router)
api_router.include_router(jobs.router)
