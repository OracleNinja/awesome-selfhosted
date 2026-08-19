"""Security event queries."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from nexus.api.deps import SessionDep
from nexus.api.schemas.common import responses
from nexus.api.schemas.monitoring import EventDetail, EventPage, EventSummary
from nexus.api.security import require
from nexus.core.errors import NotFound
from nexus.core.rbac import Permission
from nexus.db.models.event import EVENT_CATEGORIES, SEVERITIES, SecurityEvent
from nexus.services.auth import AuthenticatedIdentity

router = APIRouter(prefix="/events", tags=["events"])

EventReaderDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.EVENTS_READ))]

MAX_PAGE_SIZE = 200


@router.get(
    "",
    response_model=EventPage,
    summary="Query security events",
    description=(
        "Newest first, keyset-paginated with `before_id`.\n\n"
        "**Simulated events are excluded by default.** Laboratory data is real "
        "data about an isolated environment, but it is not an observation of "
        "your network, and mixing the two silently would be dishonest. Pass "
        "`include_simulation=true` to see it, or `only_simulation=true` for the "
        "laboratory view."
    ),
    responses=responses(401, 403, 422),
)
async def query_events(
    identity: EventReaderDep,
    session: SessionDep,
    before_id: Annotated[int | None, Query(description="Return events older than this id.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    category: Annotated[str | None, Query(max_length=16)] = None,
    severity: Annotated[str | None, Query(max_length=16)] = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    device_id: Annotated[str | None, Query(max_length=36)] = None,
    sensor_name: Annotated[str | None, Query(max_length=64)] = None,
    source_ip: Annotated[str | None, Query(max_length=45)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    search: Annotated[
        str | None, Query(max_length=200, description="Substring of the summary.")
    ] = None,
    include_simulation: Annotated[bool, Query()] = False,
    only_simulation: Annotated[bool, Query()] = False,
) -> EventPage:
    statement = select(SecurityEvent).order_by(SecurityEvent.id.desc()).limit(limit + 1)

    if only_simulation:
        statement = statement.where(SecurityEvent.is_simulation.is_(True))
    elif not include_simulation:
        # The default path, and the one the partial index covers.
        statement = statement.where(SecurityEvent.is_simulation.is_(False))

    if before_id is not None:
        statement = statement.where(SecurityEvent.id < before_id)
    if category:
        if category not in EVENT_CATEGORIES:
            # Rejected rather than silently returning nothing: an empty result
            # for a typo'd filter reads as "your network is quiet".
            raise _invalid("category", category, EVENT_CATEGORIES)
        statement = statement.where(SecurityEvent.category == category)
    if severity:
        if severity not in SEVERITIES:
            raise _invalid("severity", severity, SEVERITIES)
        statement = statement.where(SecurityEvent.severity == severity)
    if kind:
        statement = statement.where(SecurityEvent.kind == kind)
    if device_id:
        statement = statement.where(SecurityEvent.device_id == device_id)
    if sensor_name:
        statement = statement.where(SecurityEvent.sensor_name == sensor_name)
    if source_ip:
        # Bound parameter against an INET column: PostgreSQL parses and
        # compares it as an address, so a malformed value is a clean error
        # rather than a string match that quietly returns nothing.
        statement = statement.where(SecurityEvent.source_ip == source_ip)
    if since is not None:
        statement = statement.where(SecurityEvent.occurred_at >= since)
    if until is not None:
        statement = statement.where(SecurityEvent.occurred_at <= until)
    if search:
        # ILIKE with an escaped pattern. The value is still a bound parameter —
        # escaping here prevents a user's `%` from turning into a wildcard that
        # scans the whole table, not SQL injection, which parameters already
        # handle.
        # Escape LIKE's own wildcards so a user searching for "50%" does not
        # get a pattern that matches everything. Built outside the f-string:
        # backslashes inside f-string expressions are a syntax error before
        # Python 3.12, and this project targets 3.11.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        statement = statement.where(SecurityEvent.summary.ilike(pattern))

    rows = list((await session.execute(statement)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    return EventPage(
        items=[EventSummary.model_validate(row) for row in rows],
        next_cursor=rows[-1].id if rows and has_more else None,
        has_more=has_more,
    )


@router.get(
    "/{event_id}",
    response_model=EventDetail,
    summary="Get one event with its full detail",
    description=(
        "Includes the normalised attributes and the original observation as "
        "received. The raw form is the evidence — if normalisation misread "
        "something, this is what was actually seen."
    ),
    responses=responses(401, 403, 404),
)
async def get_event(event_id: int, identity: EventReaderDep, session: SessionDep) -> EventDetail:
    event = await session.get(SecurityEvent, event_id)
    if event is None:
        raise NotFound("No such event.")
    return EventDetail.model_validate(event)


def _invalid(field: str, value: str, allowed) -> Exception:
    from nexus.core.errors import ValidationFailed

    return ValidationFailed(
        f"Unknown {field}: {value!r}.",
        details={"field": field, "allowed": sorted(allowed)},
    )
