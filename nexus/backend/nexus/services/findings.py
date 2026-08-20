"""Queries and triage over findings.

Routes stay thin: filtering, evidence assembly, triage and the status roll-up
live here, so the same logic is available to a future CLI or streaming endpoint
without going through HTTP.

One rule runs through all of it: **a query never invents a state**. A filter
value that is not a known enum member is a ``422``, not an empty page; a
detector that cannot run is reported with its reason, not omitted; and a device
with no assessment is ``NOT_ASSESSED``, not zero.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.core.errors import Conflict, NotFound, ValidationFailed
from nexus.db.base import utcnow
from nexus.db.models.device import Device
from nexus.db.models.event import SEVERITIES, SecurityEvent
from nexus.db.models.finding import (
    ACTIVE_FINDING_STATUSES,
    DETECTION_TYPES,
    EVENT_STREAM,
    FINDING_STATUSES,
    DetectionState,
    DeviceRiskAssessment,
    Finding,
    FindingObservation,
)
from nexus.detection.config import DetectionConfig, LoadedConfig, load_config
from nexus.detection.risk import RiskService
from nexus.detection.rules import ALL_DETECTORS
from nexus.services.audit import AuditAction, AuditActor, AuditService

# An analysis pass is expected at least once a minute (see the scheduler). If
# events are waiting and nothing has run for this long, something is wrong —
# most often no worker process is running — and the status endpoint says so
# rather than reporting a healthy-looking backlog.
STALL_AFTER_SECONDS = 300

MAX_EVIDENCE_ROWS = 200


@dataclass(frozen=True)
class FindingFilters:
    status: str | None = None
    severity: str | None = None
    detection_type: str | None = None
    detector: str | None = None
    device_id: uuid.UUID | None = None
    min_confidence: int | None = None
    since: datetime | None = None
    until: datetime | None = None
    active_only: bool = False
    include_simulation: bool = False
    only_simulation: bool = False


def _invalid(field: str, value: str, allowed: Any) -> ValidationFailed:
    return ValidationFailed(
        f"Unknown {field}: {value!r}.",
        details={"field": field, "allowed": sorted(allowed)},
    )


def build_query(filters: FindingFilters) -> Select[Any]:
    """Translate filters into a statement, rejecting unknown enum values.

    An unknown filter value returning an empty list would read as "your network
    is quiet", which is the most dangerous wrong answer a security console can
    give. It is a validation error instead.
    """
    statement = select(Finding)

    if filters.only_simulation:
        statement = statement.where(Finding.is_simulation.is_(True))
    elif not filters.include_simulation:
        statement = statement.where(Finding.is_simulation.is_(False))

    if filters.status:
        if filters.status not in FINDING_STATUSES:
            raise _invalid("status", filters.status, FINDING_STATUSES)
        statement = statement.where(Finding.status == filters.status)
    if filters.active_only:
        statement = statement.where(Finding.status.in_(ACTIVE_FINDING_STATUSES))
    if filters.severity:
        if filters.severity not in SEVERITIES:
            raise _invalid("severity", filters.severity, SEVERITIES)
        statement = statement.where(Finding.severity == filters.severity)
    if filters.detection_type:
        if filters.detection_type not in DETECTION_TYPES:
            raise _invalid("detection_type", filters.detection_type, DETECTION_TYPES)
        statement = statement.where(Finding.detection_type == filters.detection_type)
    if filters.detector:
        known = {detector.id for detector in ALL_DETECTORS}
        if filters.detector not in known:
            raise _invalid("detector", filters.detector, known)
        statement = statement.where(Finding.detector == filters.detector)
    if filters.device_id is not None:
        statement = statement.where(Finding.device_id == filters.device_id)
    if filters.min_confidence is not None:
        statement = statement.where(Finding.confidence >= filters.min_confidence)
    if filters.since is not None:
        statement = statement.where(Finding.last_observed_at >= filters.since)
    if filters.until is not None:
        statement = statement.where(Finding.last_observed_at <= filters.until)

    return statement


async def list_findings(
    session: AsyncSession, filters: FindingFilters, *, limit: int, offset: int
) -> tuple[list[Finding], int]:
    """A page of findings, newest activity first, plus the total.

    Offset pagination, unlike the event feed's cursor. Findings are bounded —
    one per device per rule per distinct subject — so the page count stays small
    and an operator genuinely wants to jump to the last page. The event stream is
    unbounded, which is why it uses a keyset cursor instead. See docs/API.md.
    """
    statement = build_query(filters)

    total = int(
        (await session.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    )

    rows = (
        (
            await session.execute(
                statement.order_by(Finding.last_observed_at.desc(), Finding.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_finding(session: AsyncSession, finding_id: uuid.UUID) -> Finding:
    finding = await session.get(Finding, finding_id)
    if finding is None:
        raise NotFound("No such finding.")
    return finding


async def evidence_for(session: AsyncSession, finding: Finding) -> tuple[list[dict[str, Any]], int]:
    """Linked observations that still exist, joined to their events.

    ``observation_count`` on the finding is monotonic; this returns what remains
    after retention. The difference is reported rather than hidden — a finding
    whose evidence has aged out is still a real finding, but a console that
    showed it as fully supported would be claiming evidence it cannot produce.
    """
    rows = (
        await session.execute(
            select(FindingObservation, SecurityEvent)
            .join(SecurityEvent, SecurityEvent.id == FindingObservation.event_id)
            .where(FindingObservation.finding_id == finding.id)
            .order_by(SecurityEvent.occurred_at, SecurityEvent.id)
            .limit(MAX_EVIDENCE_ROWS)
        )
    ).all()

    available = int(
        (
            await session.execute(
                select(func.count())
                .select_from(FindingObservation)
                .where(FindingObservation.finding_id == finding.id)
            )
        ).scalar_one()
    )

    observations = [
        {
            "event_id": event.id,
            "role": link.role,
            "linked_at": link.linked_at,
            "occurred_at": event.occurred_at,
            "received_at": event.received_at,
            "sensor_name": event.sensor_name,
            "driver": event.driver,
            "kind": event.kind,
            "category": event.category,
            "severity": event.severity,
            "summary": event.summary,
            "source_ip": event.source_ip,
            "destination_ip": event.destination_ip,
            "destination_port": event.destination_port,
            "protocol": event.protocol,
            "mac_address": event.mac_address,
            "raw_retained": event.raw is not None,
            "is_simulation": event.is_simulation,
        }
        for link, event in rows
    ]
    return observations, available


def evidence_state(finding: Finding, available: int) -> str:
    """COMPLETE, PARTIAL or PRUNED — never silently COMPLETE."""
    if finding.observation_count == 0:
        return "COMPLETE" if available == 0 else "PARTIAL"
    if available >= finding.observation_count:
        return "COMPLETE"
    if available == 0:
        return "PRUNED"
    return "PARTIAL"


async def triage(
    session: AsyncSession,
    settings: Settings,
    finding: Finding,
    *,
    status: str,
    note: str,
    actor: AuditActor,
    user_id: uuid.UUID | None,
) -> Finding:
    """Change a finding's triage state, audit it, and re-derive the risk.

    Risk is recomputed rather than adjusted: resolving a finding removes it from
    the active set and the device's score is recalculated from what is left. A
    triage decision that did not move the score would make the whole workflow
    decorative.
    """
    if status not in FINDING_STATUSES:
        raise _invalid("status", status, FINDING_STATUSES)

    previous = finding.status
    if previous == status:
        raise Conflict(
            f"This finding is already {status}.",
            details={"status": status},
        )

    finding.status = status
    finding.triage_note = note
    finding.triaged_at = utcnow()
    finding.triaged_by_user_id = user_id

    await AuditService(session, settings).record(
        AuditAction.DETECTION_FINDING_TRIAGED,
        actor=actor,
        target_type="finding",
        target_id=str(finding.id),
        target_label=finding.detection_type,
        reason=note,
        details={
            "previous_status": previous,
            "status": status,
            "detector": finding.detector,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "device_id": str(finding.device_id) if finding.device_id else None,
            "is_simulation": finding.is_simulation,
        },
    )

    if finding.device_id is not None:
        device = await session.get(Device, finding.device_id)
        if device is not None:
            loaded = await load_config(session)
            await session.flush()  # the status change must be visible to the query
            await RiskService(session).rescore(device, config=loaded.config)

    return finding


async def device_risk(
    session: AsyncSession, device: Device, loaded: LoadedConfig
) -> dict[str, Any]:
    """A device's current risk with its derivation, or an honest NOT_ASSESSED."""
    latest = (
        await session.execute(
            select(DeviceRiskAssessment)
            .where(DeviceRiskAssessment.device_id == device.id)
            .order_by(DeviceRiskAssessment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if device.risk_score is None or latest is None:
        return {
            "device_id": device.id,
            "state": "NOT_ASSESSED",
            "risk_score": None,
            "risk_level": device.risk_level,
            "risk_updated_at": device.risk_updated_at,
            "weights_source": loaded.source,
            "factors": [],
            "contributing_findings": [],
            "is_simulation": device.is_simulation,
        }

    finding_ids = [uuid.UUID(value) for value in latest.contributing_finding_ids]
    findings: list[Finding] = []
    if finding_ids:
        findings = list(
            (await session.execute(select(Finding).where(Finding.id.in_(finding_ids))))
            .scalars()
            .all()
        )
        findings.sort(key=lambda item: (item.last_observed_at, item.id), reverse=True)

    return {
        "device_id": device.id,
        "state": "ASSESSED",
        "risk_score": device.risk_score,
        "risk_level": device.risk_level,
        "risk_updated_at": device.risk_updated_at,
        "previous_score": latest.previous_score,
        "previous_level": latest.previous_level,
        "window_started_at": latest.window_started_at,
        "window_ended_at": latest.window_ended_at,
        "weights_fingerprint": latest.weights_fingerprint,
        "weights_source": loaded.source,
        "factors": latest.factors,
        "contributing_findings": findings,
        "is_simulation": device.is_simulation,
    }


async def risk_history(
    session: AsyncSession, device: Device, *, limit: int, offset: int
) -> tuple[list[DeviceRiskAssessment], int]:
    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(DeviceRiskAssessment)
                .where(DeviceRiskAssessment.device_id == device.id)
            )
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                select(DeviceRiskAssessment)
                .where(DeviceRiskAssessment.device_id == device.id)
                .order_by(DeviceRiskAssessment.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def detection_status(
    session: AsyncSession, settings: Settings, loaded: LoadedConfig
) -> dict[str, Any]:
    """The engine's own account of itself, with no optimistic defaults."""
    state = (
        await session.execute(select(DetectionState).where(DetectionState.stream == EVENT_STREAM))
    ).scalar_one_or_none()

    watermark = state.last_event_id if state else 0
    pending = int(
        (
            await session.execute(
                select(func.count()).select_from(SecurityEvent).where(SecurityEvent.id > watermark)
            )
        ).scalar_one()
    )

    analysis_state = _analysis_state(state, pending)

    detectors = await _detector_status(session, settings, loaded.config)

    assessed = int(
        (
            await session.execute(
                select(func.count()).select_from(Device).where(Device.risk_score.is_not(None))
            )
        ).scalar_one()
    )
    unassessed = int(
        (
            await session.execute(
                select(func.count()).select_from(Device).where(Device.risk_score.is_(None))
            )
        ).scalar_one()
    )

    by_status = await _counts(session, Finding.status)
    by_severity = await _counts(session, Finding.severity)

    simulation = int(
        (
            await session.execute(
                select(func.count()).select_from(Finding).where(Finding.is_simulation.is_(True))
            )
        ).scalar_one()
    )
    real = int(
        (
            await session.execute(
                select(func.count()).select_from(Finding).where(Finding.is_simulation.is_(False))
            )
        ).scalar_one()
    )

    return {
        "analysis": {
            "state": analysis_state,
            "last_run_at": state.last_run_at if state else None,
            "last_run_status": state.last_run_status if state else None,
            "watermark_event_id": watermark,
            "pending_events": pending,
            "events_examined_total": state.events_examined if state else 0,
            "findings_created_total": state.findings_created if state else 0,
            "findings_updated_total": state.findings_updated if state else 0,
        },
        "detectors": detectors,
        "risk": {
            "weights_source": loaded.source,
            "weights_detail": loaded.detail,
            "weights_fingerprint": loaded.config.fingerprint(),
            "thresholds": {
                "medium": loaded.config.thresholds.medium,
                "high": loaded.config.thresholds.high,
                "critical": loaded.config.thresholds.critical,
            },
            "devices_assessed": assessed,
            "devices_not_assessed": unassessed,
        },
        "findings_by_status": by_status,
        "findings_by_severity": by_severity,
        "findings_real": real,
        "findings_simulation": simulation,
    }


def _analysis_state(state: DetectionState | None, pending: int) -> str:
    """Distinguish "caught up" from "nobody is running the analysis".

    A finite laboratory scenario that has finished producing events leaves
    ``pending == 0`` and a recent successful run: that is ``IDLE``, a healthy
    state, and must not be reported as a fault. A backlog with no recent run is
    ``STALLED``, which is a fault and says so.
    """
    if state is None or state.last_run_at is None:
        return "NEVER_RUN"
    if pending == 0:
        return "IDLE"
    age = (utcnow() - state.last_run_at).total_seconds()
    if age > STALL_AFTER_SECONDS:
        return "STALLED"
    return "BACKLOG"


async def _detector_status(
    session: AsyncSession, settings: Settings, config: DetectionConfig
) -> list[dict[str, Any]]:
    counts = {
        (row[0], row[1]): int(row[2])
        for row in (
            await session.execute(
                select(Finding.detector, Finding.is_simulation, func.count()).group_by(
                    Finding.detector, Finding.is_simulation
                )
            )
        ).all()
    }

    rows: list[dict[str, Any]] = []
    for detector in ALL_DETECTORS:
        for is_simulation in (False, True):
            availability = detector.availability(config, settings, is_simulation=is_simulation)
            rows.append(
                {
                    "id": detector.id,
                    "detection_type": detector.detection_type,
                    "version": detector.version,
                    "description": detector.description,
                    "scope": "SIMULATION" if is_simulation else "REAL_NETWORK",
                    "state": availability.state,
                    "detail": availability.detail,
                    "remedy": availability.remedy,
                    "findings_total": counts.get((detector.id, is_simulation), 0),
                }
            )
    return rows


async def _counts(session: AsyncSession, column: Any) -> dict[str, int]:
    rows = (await session.execute(select(column, func.count()).group_by(column))).all()
    return {str(row[0]): int(row[1]) for row in rows}


__all__ = [
    "STALL_AFTER_SECONDS",
    "FindingFilters",
    "detection_status",
    "device_risk",
    "evidence_for",
    "evidence_state",
    "get_finding",
    "list_findings",
    "risk_history",
    "triage",
]
