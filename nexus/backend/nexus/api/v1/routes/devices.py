"""Device inventory and profiles."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.middleware import client_ip
from nexus.api.schemas.common import responses
from nexus.api.schemas.monitoring import (
    DeviceDetail,
    DevicePage,
    DeviceSummary,
    EventSummary,
    UpdateDeviceRequest,
)
from nexus.api.security import AuditDep, actor_from, require
from nexus.core.errors import NotFound
from nexus.core.rbac import Permission
from nexus.db.base import utcnow
from nexus.db.models.device import Device
from nexus.db.models.event import SecurityEvent
from nexus.services.audit import AuditAction
from nexus.services.auth import AuthenticatedIdentity

router = APIRouter(prefix="/devices", tags=["devices"])

DeviceReaderDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.DEVICES_READ))]
DeviceAnnotatorDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.DEVICES_ANNOTATE))]

RECENT_EVENT_LIMIT = 25


@router.get(
    "",
    response_model=DevicePage,
    summary="List devices",
    description=(
        "The inventory, most recently seen first. Simulated (laboratory) "
        "devices are excluded unless you ask for them.\n\n"
        "`risk_score` is null for a device the risk engine has not assessed. "
        "That is different from a score of zero, and clients must render it "
        "differently — 'not assessed' is not 'assessed and safe'."
    ),
    responses=responses(401, 403, 422),
)
async def list_devices(
    identity: DeviceReaderDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    state: Annotated[str | None, Query(max_length=16)] = None,
    risk_level: Annotated[str | None, Query(max_length=16)] = None,
    search: Annotated[str | None, Query(max_length=128)] = None,
    include_simulation: Annotated[bool, Query()] = False,
    only_simulation: Annotated[bool, Query()] = False,
) -> DevicePage:
    conditions: list[Any] = []
    if only_simulation:
        conditions.append(Device.is_simulation.is_(True))
    elif not include_simulation:
        conditions.append(Device.is_simulation.is_(False))
    if state:
        conditions.append(Device.state == state)
    if risk_level:
        conditions.append(Device.risk_level == risk_level)
    if search:
        # Escape LIKE's own wildcards so a user searching for "50%" does not
        # get a pattern that matches everything. Built outside the f-string:
        # backslashes inside f-string expressions are a syntax error before
        # Python 3.12, and this project targets 3.11.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append(
            Device.label.ilike(pattern)
            | Device.hostname.ilike(pattern)
            | Device.mac_address.ilike(pattern)
            # INET has no ILIKE; cast to text for the search box, which is a
            # convenience path and not the indexed lookup used by ingestion.
            | func.text(Device.ip_address).ilike(pattern)
        )

    # Offset pagination is acceptable here and not for events: a device
    # inventory is bounded by how many devices exist on one network — hundreds,
    # not millions — and it does not grow while the operator reads it.
    total = (
        await session.execute(select(func.count()).select_from(Device).where(*conditions))
    ).scalar_one()

    rows = (
        (
            await session.execute(
                select(Device)
                .where(*conditions)
                .order_by(Device.last_seen_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return DevicePage(
        items=[DeviceSummary.model_validate(row) for row in rows],
        total=total,
        has_more=offset + len(rows) < total,
    )


@router.get(
    "/{device_id}",
    response_model=DeviceDetail,
    summary="Get a device profile",
    description="Includes the most recent events involving this device and a 24-hour event count.",
    responses=responses(401, 403, 404),
)
async def get_device(
    device_id: uuid.UUID, identity: DeviceReaderDep, session: SessionDep
) -> DeviceDetail:
    device = await session.get(Device, device_id)
    if device is None:
        raise NotFound("No such device.")

    recent = (
        (
            await session.execute(
                select(SecurityEvent)
                .where(SecurityEvent.device_id == device_id)
                .order_by(SecurityEvent.id.desc())
                .limit(RECENT_EVENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    since = utcnow() - timedelta(hours=24)
    count_24h = (
        await session.execute(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.device_id == device_id, SecurityEvent.occurred_at >= since)
        )
    ).scalar_one()

    detail = DeviceDetail.model_validate(device)
    detail.recent_events = [EventSummary.model_validate(event) for event in recent]
    detail.event_count_24h = count_24h
    return detail


@router.patch(
    "/{device_id}",
    response_model=DeviceSummary,
    summary="Annotate a device",
    description=(
        "Sets the operator-owned fields: label, notes, tags, and trust. "
        "Sensors never overwrite these — a human writing 'kitchen thermostat' "
        "is worth more than any fingerprinting, and losing it to an automatic "
        "update would teach operators not to bother.\n\n"
        "Marking a device trusted suppresses risk *escalation*, not recording. "
        "A trusted device that starts port-scanning still produces events."
    ),
    responses=responses(401, 403, 404, 422),
)
async def update_device(
    device_id: uuid.UUID,
    payload: UpdateDeviceRequest,
    request: Request,
    identity: DeviceAnnotatorDep,
    session: SessionDep,
    context: ContextDep,
    audit: AuditDep,
) -> DeviceSummary:
    device = await session.get(Device, device_id)
    if device is None:
        raise NotFound("No such device.")

    changes: dict[str, object] = {}
    if payload.label is not None and payload.label != device.label:
        changes["label"] = {"from": device.label, "to": payload.label}
        device.label = payload.label
    if payload.notes is not None and payload.notes != device.notes:
        changes["notes"] = {"changed": True}  # the text itself can be long and private
        device.notes = payload.notes
    if payload.tags is not None:
        cleaned = [tag.strip()[:32] for tag in payload.tags if tag.strip()][:32]
        if cleaned != device.tags:
            changes["tags"] = {"from": device.tags, "to": cleaned}
            device.tags = cleaned
    if payload.is_trusted is not None and payload.is_trusted != device.is_trusted:
        changes["is_trusted"] = {"from": device.is_trusted, "to": payload.is_trusted}
        device.is_trusted = payload.is_trusted

    if changes:
        # Trust changes what the platform will escalate, so it is a
        # security-relevant edit and is audited like one.
        await audit.record(
            AuditAction.SETTING_CHANGED,
            actor=actor_from(identity),
            target_type="device",
            target_id=str(device.id),
            target_label=device.label or device.mac_address or str(device.ip_address),
            source_ip=client_ip(request, context.settings),
            details={"changes": changes},
        )

    return DeviceSummary.model_validate(device)
