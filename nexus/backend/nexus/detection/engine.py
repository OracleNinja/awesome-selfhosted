"""The analysis pass: observations in, findings and risk out.

Shape of the pipeline
---------------------
::

    watermark ──▶ fetch a bounded batch of security_events
                     │
                     ├─ project to canonical Observations (provenance preserved)
                     ├─ split by scope: real | simulation   (never mixed)
                     ├─ run each available detector over each scope
                     ├─ persist candidates by fingerprint (INSERT … ON CONFLICT)
                     ├─ link evidence (PK (finding_id, event_id) makes it a no-op on replay)
                     ├─ recompute risk for every touched device (pure function)
                     ├─ audit what changed, on the existing hash chain
                     └─ advance the watermark
                              │
                              ▼  one transaction, owned by the worker

Everything above happens in the caller's transaction. The watermark advances
only if the findings it produced are committed, so a crash mid-pass re-analyses
the same events — and re-analysis is harmless, which is the entire point of the
fingerprint and evidence-link constraints.

Why one pass instead of a queue of event ids
--------------------------------------------
Handing each event to a job would mean a job table larger than the event table,
and would make cross-event rules (which need to count occurrences in a window)
either impossible or dependent on job ordering. A watermark over a monotonic id
gives ordered, exactly-bounded, restartable batches with O(1) state. See
:class:`~nexus.db.models.finding.DetectionState`.

Concurrency
-----------
The pass takes a transaction-scoped advisory lock. It is a ``try`` lock, not a
blocking one: if another worker is already analysing, this one returns
immediately and its job succeeds having done nothing. Blocking would tie up a
worker slot to accomplish exactly the work the other pass is already doing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.core.logging import get_logger
from nexus.db.base import affected_rows, utcnow
from nexus.db.models.device import Device
from nexus.db.models.event import SecurityEvent
from nexus.db.models.finding import (
    EVENT_STREAM,
    DetectionState,
    Finding,
    FindingObservation,
)
from nexus.detection.base import Candidate, DetectionContext, Detector
from nexus.detection.config import DetectionConfig, LoadedConfig, load_config
from nexus.detection.observation import from_event
from nexus.detection.risk import RiskService, score_finding
from nexus.detection.rules import ALL_DETECTORS
from nexus.services.audit import AuditAction, AuditActor, AuditService

logger = get_logger(__name__)

# Derived from ASCII "NEXUSDET", for the same reasons as the audit lock: a
# collision with another application's advisory lock on the same database is
# vanishingly unlikely, and the number is recognisable in pg_locks.
DETECTION_LOCK_KEY = 0x4E45585553444554

# How many events one pass examines. Bounded so a long outage's backlog is
# worked through over several runs instead of in one transaction that times out
# and never makes progress — the same reasoning as the retention jobs.
DEFAULT_BATCH_SIZE = 500
MAX_BATCH_SIZE = 5_000

# Beyond this many audited findings in one pass, a single summary entry is
# written instead. A burst must never be able to make the audit log unreadable,
# and the summary still records that it happened.
MAX_AUDIT_ENTRIES_PER_RUN = 25


@dataclass
class AnalysisResult:
    """What one pass actually did. Returned to the job so it is visible."""

    events_examined: int = 0
    findings_created: int = 0
    findings_updated: int = 0
    observations_linked: int = 0
    devices_rescored: int = 0
    risk_changed: int = 0
    watermark_from: int = 0
    watermark_to: int = 0
    backlog_remaining: int = 0
    skipped_locked: bool = False
    detectors_run: list[str] = field(default_factory=list)
    detectors_unavailable: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "events_examined": self.events_examined,
            "findings_created": self.findings_created,
            "findings_updated": self.findings_updated,
            "observations_linked": self.observations_linked,
            "devices_rescored": self.devices_rescored,
            "risk_changed": self.risk_changed,
            "watermark_from": self.watermark_from,
            "watermark_to": self.watermark_to,
            "backlog_remaining": self.backlog_remaining,
            "skipped_locked": self.skipped_locked,
            "detectors_run": self.detectors_run,
            "detectors_unavailable": self.detectors_unavailable,
        }


class DetectionEngine:
    """Runs detectors over new observations and keeps risk in step.

    Holds the caller's session: findings, evidence links, risk assessments, the
    audit entries describing them and the watermark advance are all one
    transaction. There is no window in which a finding exists but its audit
    entry does not, and none in which the watermark has moved past events whose
    findings were rolled back.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        detectors: tuple[Detector, ...] = ALL_DETECTORS,
    ) -> None:
        self._session = session
        self._settings = settings
        self._detectors = detectors
        self._audit = AuditService(session, settings)
        self._risk = RiskService(session)

    async def analyze_pending(
        self, *, batch_size: int = DEFAULT_BATCH_SIZE, now: datetime | None = None
    ) -> AnalysisResult:
        """Analyse the next batch of events after the watermark."""
        moment = now or utcnow()
        size = max(1, min(MAX_BATCH_SIZE, batch_size))
        result = AnalysisResult()

        acquired = (
            await self._session.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": DETECTION_LOCK_KEY}
            )
        ).scalar_one()
        if not acquired:
            result.skipped_locked = True
            logger.debug("detection_pass_skipped_locked")
            return result

        state = await self._load_state()
        result.watermark_from = state.last_event_id
        result.watermark_to = state.last_event_id

        loaded = await load_config(self._session)
        config = loaded.config

        events = (
            (
                await self._session.execute(
                    select(SecurityEvent)
                    .where(SecurityEvent.id > state.last_event_id)
                    .order_by(SecurityEvent.id)
                    .limit(size)
                )
            )
            .scalars()
            .all()
        )

        if not events:
            state.last_run_at = moment
            state.last_run_status = "SUCCEEDED"
            state.updated_at = moment
            return result

        observations = [from_event(event) for event in events]
        result.events_examined = len(observations)
        highest_id = max(item.event_id for item in observations)

        touched_devices: set[uuid.UUID] = set()
        created_findings: list[Finding] = []

        # Two scopes, evaluated independently. Laboratory observations and real
        # observations never appear in the same context, so no rule can compare
        # one against the other even by accident.
        for is_simulation in (False, True):
            scope = tuple(item for item in observations if item.is_simulation is is_simulation)
            if not scope:
                continue
            context = DetectionContext(
                session=self._session,
                settings=self._settings,
                config=config,
                observations=scope,
                is_simulation=is_simulation,
                first_event_id=min(item.event_id for item in scope),
                last_event_id=max(item.event_id for item in scope),
                now=moment,
            )
            await self._run_detectors(context, result, touched_devices, created_findings, loaded)

        # Risk is recomputed for every device a finding touched. Pure function
        # of the device's current findings, so this is safe to repeat.
        for device_id in sorted(touched_devices, key=str):
            device = await self._session.get(Device, device_id)
            if device is None:
                continue
            previous_level = device.risk_level
            _, changed = await self._risk.rescore(device, config=config, now=moment)
            result.devices_rescored += 1
            if changed:
                result.risk_changed += 1
                if device.risk_level != previous_level:
                    await self._audit_risk_change(device, previous_level)

        await self._audit_findings(created_findings)

        state.last_event_id = highest_id
        state.last_run_at = moment
        state.last_run_status = "SUCCEEDED"
        state.events_examined += result.events_examined
        state.findings_created += result.findings_created
        state.findings_updated += result.findings_updated
        state.updated_at = moment
        result.watermark_to = highest_id

        result.backlog_remaining = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(SecurityEvent)
                    .where(SecurityEvent.id > highest_id)
                )
            ).scalar_one()
        )

        logger.info("detection_pass_completed", extra=result.as_dict())
        return result

    # ------------------------------------------------------------ internals --

    async def _run_detectors(
        self,
        context: DetectionContext,
        result: AnalysisResult,
        touched_devices: set[uuid.UUID],
        created_findings: list[Finding],
        loaded: LoadedConfig,
    ) -> None:
        for detector in self._detectors:
            availability = detector.availability(
                context.config, context.settings, is_simulation=context.is_simulation
            )
            scope_name = "simulation" if context.is_simulation else "real"
            label = f"{detector.id}[{scope_name}]"
            if not availability.can_run:
                # Recorded rather than silently skipped: "this rule has never
                # run" and "this rule found nothing" are different facts and the
                # status endpoint reports both.
                result.detectors_unavailable[label] = availability.state
                continue
            result.detectors_run.append(label)

            candidates = await detector.evaluate(context)
            for candidate in candidates:
                finding, created = await self._persist(candidate, context.config)
                if created:
                    result.findings_created += 1
                    created_findings.append(finding)
                else:
                    result.findings_updated += 1
                result.observations_linked += await self._link_observations(finding, candidate)
                if candidate.device_id is not None:
                    touched_devices.add(candidate.device_id)

        if loaded.is_default and loaded.source == "DEFAULT_AFTER_INVALID_CONFIGURATION":
            logger.warning(
                "detection_using_default_config",
                extra={"reason": loaded.detail},
            )

    async def _persist(self, candidate: Candidate, config: DetectionConfig) -> tuple[Finding, bool]:
        """Insert or extend the finding this candidate identifies.

        The uniqueness of ``fingerprint`` is enforced by the database, so two
        workers reaching this line concurrently produce one row and one update
        rather than two rows. An application-level "select then insert" would
        not: between the select and the insert, the other transaction commits.
        """
        fingerprint = candidate.fingerprint()
        finding_risk = score_finding(candidate.severity, candidate.confidence)
        risk_factors = [
            {
                "code": "FINDING_SEVERITY",
                "value": candidate.severity,
                "detail": "How serious this finding is if the conclusion is correct.",
            },
            {
                "code": "FINDING_CONFIDENCE",
                "value": candidate.confidence,
                "detail": "How likely the detector considers the conclusion to be correct.",
            },
        ]

        values = {
            "id": uuid.uuid4(),
            "fingerprint": fingerprint,
            "detector": candidate.detector,
            "rule_version": candidate.rule_version,
            "detection_type": candidate.detection_type,
            "severity": candidate.severity,
            "confidence": candidate.confidence,
            "risk_score": finding_risk,
            "status": "OPEN",
            "first_observed_at": candidate.first_observed_at,
            "last_observed_at": candidate.last_observed_at,
            "device_id": candidate.device_id,
            "observation_count": 0,
            "evidence": candidate.evidence,
            "explanation": candidate.explanation,
            "risk_factors": risk_factors,
            "is_simulation": candidate.is_simulation,
        }

        statement = pg_insert(Finding).values(**values)
        excluded = statement.excluded

        update = {
            # The window only ever widens: a later pass that sees the same thing
            # again extends it, and an earlier event discovered out of order
            # pulls the start back.
            "first_observed_at": func.least(Finding.first_observed_at, excluded.first_observed_at),
            "last_observed_at": func.greatest(Finding.last_observed_at, excluded.last_observed_at),
            "severity": excluded.severity,
            "confidence": excluded.confidence,
            "risk_score": excluded.risk_score,
            "risk_factors": excluded.risk_factors,
            "evidence": excluded.evidence,
            "explanation": excluded.explanation,
            "rule_version": excluded.rule_version,
            # A RESOLVED finding whose subject happens again is reopened: an
            # operator resolving "this is handled" must not silence the rule
            # permanently. SUPPRESSED and FALSE_POSITIVE are deliberate
            # instructions from a human and are left alone — that is the
            # difference between the three, and collapsing it would make
            # suppression useless.
            "status": case(
                (
                    (Finding.status == "RESOLVED")
                    & (excluded.last_observed_at > Finding.last_observed_at),
                    "OPEN",
                ),
                else_=Finding.status,
            ),
            "updated_at": func.now(),
        }

        # `xmax = 0` is PostgreSQL's own marker for "this row was inserted by
        # the current statement rather than updated": an inserted tuple has no
        # deleting transaction id. It is the only way to tell the two apart from
        # a single upsert's RETURNING clause.
        returning = statement.on_conflict_do_update(
            index_elements=["fingerprint"], set_=update
        ).returning(Finding.id, text("(xmax = 0) AS was_inserted"))

        row = (await self._session.execute(returning)).one()
        finding_id, was_inserted = row[0], bool(row[1])

        finding = await self._session.get(Finding, finding_id, populate_existing=True)
        if finding is None:  # pragma: no cover - the row was just written
            raise RuntimeError("Finding disappeared immediately after upsert.")
        return finding, was_inserted

    async def _link_observations(self, finding: Finding, candidate: Candidate) -> int:
        """Attach evidence, counting only links that did not already exist.

        The composite primary key does the work: a replayed pass inserts nothing
        and the returned count is zero, so ``observation_count`` cannot drift
        upwards under retry. This is the second half of the idempotency
        guarantee described in :mod:`nexus.db.models.finding`.
        """
        if not candidate.observations:
            return 0

        # De-duplicate within the candidate itself; an event cited as both
        # trigger and supporting evidence would otherwise make the multi-row
        # INSERT conflict with itself, which ON CONFLICT does not cover.
        seen: dict[int, str] = {}
        for event_id, role in candidate.observations:
            seen.setdefault(event_id, role)

        rows = [
            {"finding_id": finding.id, "event_id": event_id, "role": role}
            for event_id, role in seen.items()
        ]
        inserted = affected_rows(
            await self._session.execute(
                pg_insert(FindingObservation)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["finding_id", "event_id"])
            )
        )
        if inserted:
            finding.observation_count += inserted
        return inserted

    async def _load_state(self) -> DetectionState:
        """Fetch the watermark row, creating it on first run.

        ``FOR UPDATE`` on top of the advisory lock is redundant today and cheap;
        it means a future caller that forgets the advisory lock still cannot
        interleave two watermark advances.
        """
        await self._session.execute(
            pg_insert(DetectionState)
            .values(stream=EVENT_STREAM)
            .on_conflict_do_nothing(index_elements=["stream"])
        )
        state = (
            await self._session.execute(
                select(DetectionState)
                .where(DetectionState.stream == EVENT_STREAM)
                .with_for_update()
            )
        ).scalar_one()
        return state

    async def _audit_findings(self, findings: list[Finding]) -> None:
        """Record newly created findings on the existing hash chain."""
        if not findings:
            return

        if len(findings) > MAX_AUDIT_ENTRIES_PER_RUN:
            await self._audit.record(
                AuditAction.DETECTION_FINDINGS_CREATED,
                actor=AuditActor.system("detection"),
                target_type="detection",
                reason="Analysis pass created more findings than are audited individually.",
                details={
                    "created": len(findings),
                    "audited_individually": False,
                    "by_type": _count_types(findings),
                    "simulation": sum(1 for item in findings if item.is_simulation),
                },
            )
            return

        for finding in findings:
            await self._audit.record(
                AuditAction.DETECTION_FINDING_CREATED,
                actor=AuditActor.system("detection"),
                target_type="finding",
                target_id=str(finding.id),
                target_label=finding.detection_type,
                reason=finding.explanation[:500],
                details={
                    "detector": finding.detector,
                    "rule_version": finding.rule_version,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "risk_score": finding.risk_score,
                    "device_id": str(finding.device_id) if finding.device_id else None,
                    "is_simulation": finding.is_simulation,
                    "observation_count": finding.observation_count,
                },
            )

    async def _audit_risk_change(self, device: Device, previous_level: str) -> None:
        """Record a change of risk *band*, not every point of movement.

        A score that ticks from 41 to 43 is not a security event; a device
        crossing from MEDIUM to HIGH is. Auditing every recomputation would bury
        the transitions that matter under noise, which is a way of losing them.
        """
        await self._audit.record(
            AuditAction.DEVICE_RISK_CHANGED,
            actor=AuditActor.system("detection"),
            target_type="device",
            target_id=str(device.id),
            target_label=device.label or device.hostname or device.mac_address,
            reason=f"Risk level changed from {previous_level} to {device.risk_level}.",
            details={
                "previous_level": previous_level,
                "level": device.risk_level,
                "score": device.risk_score,
                "is_simulation": device.is_simulation,
            },
        )


def _count_types(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.detection_type] = counts.get(finding.detection_type, 0) + 1
    return counts


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DETECTION_LOCK_KEY",
    "AnalysisResult",
    "DetectionEngine",
]
