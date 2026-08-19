"""Sensor configuration and status."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.middleware import client_ip
from nexus.api.schemas.admin import ReasonRequest
from nexus.api.schemas.common import OkResponse, responses
from nexus.api.schemas.monitoring import (
    CreateSensorRequest,
    SensorDriverInfo,
    SensorSummary,
    UpdateSensorRequest,
)
from nexus.api.security import AuditDep, actor_from, require
from nexus.core.errors import Conflict, NotFound, ValidationFailed
from nexus.core.rbac import Permission
from nexus.db.models.sensor import Sensor as SensorRow
from nexus.sensors import SENSOR_DRIVERS, resolve_driver
from nexus.services.audit import AuditAction
from nexus.services.auth import AuthenticatedIdentity

router = APIRouter(prefix="/sensors", tags=["sensors"])

SensorReaderDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.SENSORS_READ))]
SensorManagerDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.SENSORS_MANAGE))]


@router.get(
    "",
    response_model=list[SensorSummary],
    summary="List configured sensors",
    description=(
        "Includes each sensor's live status and, when it is not running, the "
        "reason. `NOT_AVAILABLE` means this host cannot support the driver; "
        "`NOT_CONFIGURED` means it has not been set up. Neither is an error, "
        "and neither is 'no events found'."
    ),
    responses=responses(401, 403),
)
async def list_sensors(identity: SensorReaderDep, session: SessionDep) -> list[SensorSummary]:
    rows = (await session.execute(select(SensorRow).order_by(SensorRow.name))).scalars().all()
    return [SensorSummary.model_validate(row) for row in rows]


@router.get(
    "/drivers",
    response_model=list[SensorDriverInfo],
    summary="List sensor drivers and whether they can run here",
    description=(
        "Each driver reports its availability **on this host right now**, with "
        "a remedy when it is unavailable — for example the exact `setcap` "
        "command that grants packet capture without running as root.\n\n"
        "Drivers that produce simulated laboratory data are marked as such."
    ),
    responses=responses(401, 403),
)
async def list_drivers(identity: SensorReaderDep, context: ContextDep) -> list[SensorDriverInfo]:
    infos: list[SensorDriverInfo] = []
    for name, driver_class in SENSOR_DRIVERS.items():
        # Availability is evaluated with empty configuration, so a driver that
        # needs settings correctly reports NOT_CONFIGURED rather than claiming
        # it is ready.
        report = driver_class.check_availability({}, context.settings)
        infos.append(
            SensorDriverInfo(
                driver=name,
                description=driver_class.description,
                produces_simulated_data=driver_class.produces_simulated_data,
                requires_privileges=driver_class.requires_privileges,
                availability=report.status.value,
                availability_detail=report.detail,
                remedy=report.remedy,
            )
        )
    return infos


@router.post(
    "",
    response_model=SensorSummary,
    status_code=201,
    summary="Configure a sensor",
    description=(
        "Validates the configuration against the driver's own schema before "
        "storing it, so a sensor is never persisted in a state that cannot "
        "start.\n\nSecrets do not belong in `config` — a sensor that needs a "
        "credential takes the *name* of an environment variable instead."
    ),
    responses=responses(401, 403, 409, 422),
)
async def create_sensor(
    payload: CreateSensorRequest,
    request: Request,
    identity: SensorManagerDep,
    session: SessionDep,
    context: ContextDep,
    audit: AuditDep,
) -> SensorSummary:
    driver_class = resolve_driver(payload.driver)

    existing = await session.execute(select(SensorRow.id).where(SensorRow.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise Conflict("A sensor with that name already exists.")

    config = driver_class.validate_config(payload.config or {})
    report = driver_class.check_availability(config, context.settings)

    row = SensorRow(
        name=payload.name,
        driver=payload.driver,
        description=payload.description,
        enabled=payload.enabled,
        config=config,
        # Recorded up front so the sensors screen tells the truth immediately,
        # before the manager has had a chance to start it.
        status="STOPPED" if report.is_available else report.status.value,
        status_detail=report.detail,
        # From the driver class, so a laboratory driver cannot be configured to
        # look like a real sensor.
        is_simulation=driver_class.produces_simulated_data,
    )
    session.add(row)
    await session.flush()

    await audit.record(
        AuditAction.SETTING_CHANGED,
        actor=actor_from(identity),
        target_type="sensor",
        target_id=str(row.id),
        target_label=row.name,
        reason="Sensor created.",
        source_ip=client_ip(request, context.settings),
        details={"driver": row.driver, "enabled": row.enabled, "simulation": row.is_simulation},
    )
    _request_reload(context)
    return SensorSummary.model_validate(row)


@router.patch(
    "/{sensor_id}",
    response_model=SensorSummary,
    summary="Update a sensor",
    description=(
        "Changing what the platform watches is security-relevant, so a written "
        "reason is required and recorded. Configuration is re-validated against "
        "the driver, and the sensor manager reloads without restarting NEXUS — "
        "restarting would interrupt every other sensor."
    ),
    responses=responses(401, 403, 404, 422),
)
async def update_sensor(
    sensor_id: uuid.UUID,
    payload: UpdateSensorRequest,
    request: Request,
    identity: SensorManagerDep,
    session: SessionDep,
    context: ContextDep,
    audit: AuditDep,
) -> SensorSummary:
    row = await session.get(SensorRow, sensor_id)
    if row is None:
        raise NotFound("No such sensor.")

    driver_class = resolve_driver(row.driver)
    changes: dict[str, object] = {}

    if payload.description is not None and payload.description != row.description:
        changes["description"] = {"from": row.description, "to": payload.description}
        row.description = payload.description
    if payload.enabled is not None and payload.enabled != row.enabled:
        changes["enabled"] = {"from": row.enabled, "to": payload.enabled}
        row.enabled = payload.enabled
    if payload.config is not None:
        config = driver_class.validate_config(payload.config)
        if config != row.config:
            changes["config"] = {"from": row.config, "to": config}
            row.config = config
            report = driver_class.check_availability(config, context.settings)
            row.status_detail = report.detail

    if changes:
        await audit.record(
            AuditAction.SETTING_CHANGED,
            actor=actor_from(identity),
            target_type="sensor",
            target_id=str(row.id),
            target_label=row.name,
            reason=payload.reason,
            source_ip=client_ip(request, context.settings),
            details={"changes": changes},
        )
        _request_reload(context)

    return SensorSummary.model_validate(row)


@router.delete(
    "/{sensor_id}",
    response_model=OkResponse,
    summary="Delete a sensor",
    description=(
        "The sensor stops and its configuration is removed. Events it already "
        "produced are kept — deleting a sensor must not erase the evidence it "
        "collected — and they retain its name, which is why the name is copied "
        "onto every event rather than joined."
    ),
    responses=responses(401, 403, 404, 422),
)
async def delete_sensor(
    sensor_id: uuid.UUID,
    payload: ReasonRequest,
    request: Request,
    identity: SensorManagerDep,
    session: SessionDep,
    context: ContextDep,
    audit: AuditDep,
) -> OkResponse:
    row = await session.get(SensorRow, sensor_id)
    if row is None:
        raise NotFound("No such sensor.")

    name = row.name
    await audit.record(
        AuditAction.SETTING_CHANGED,
        actor=actor_from(identity),
        target_type="sensor",
        target_id=str(row.id),
        target_label=name,
        reason=payload.reason,
        source_ip=client_ip(request, context.settings),
        details={"deleted": True, "driver": row.driver},
    )
    await session.delete(row)
    await session.flush()
    _request_reload(context)
    return OkResponse(message=f"Sensor {name} deleted. Its events were retained.")


@router.post(
    "/{sensor_id}/check",
    response_model=SensorDriverInfo,
    summary="Re-check whether a sensor can run",
    description=(
        "Runs the driver's availability check against the current host and "
        "configuration, without starting anything. Use it after granting a "
        "capability or plugging in an interface."
    ),
    responses=responses(401, 403, 404),
)
async def check_sensor(
    sensor_id: uuid.UUID,
    identity: SensorReaderDep,
    session: SessionDep,
    context: ContextDep,
) -> SensorDriverInfo:
    row = await session.get(SensorRow, sensor_id)
    if row is None:
        raise NotFound("No such sensor.")

    driver_class = resolve_driver(row.driver)
    report = driver_class.check_availability(row.config or {}, context.settings)
    row.status_detail = report.detail
    if not report.is_available:
        row.status = report.status.value

    return SensorDriverInfo(
        driver=row.driver,
        description=driver_class.description,
        produces_simulated_data=driver_class.produces_simulated_data,
        requires_privileges=driver_class.requires_privileges,
        availability=report.status.value,
        availability_detail=report.detail,
        remedy=report.remedy,
    )


def _request_reload(context) -> None:
    """Tell the sensor manager its configuration changed.

    A *signal*, not a reload: the manager re-reads the sensors table in its own
    session, after a short debounce, from its own task.

    Reloading inline would read the database before this request's transaction
    commits, and so miss the very row the request just wrote. A FastAPI
    background task does not fix that either — background tasks run *before*
    yield-dependency teardown, which is where the commit happens. Handing the
    manager a signal and letting it choose when to look is the only version of
    this that is not a race.
    """
    if context.sensors is not None:
        context.sensors.request_reload()


_ = ValidationFailed  # re-exported for driver validation errors raised above
