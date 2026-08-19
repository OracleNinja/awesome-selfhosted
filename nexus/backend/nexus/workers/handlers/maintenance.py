"""Housekeeping jobs: retention, purging, and device state reconciliation.

Every handler here is **idempotent**, because delivery is at-least-once (see
:mod:`nexus.services.jobs`). Deleting rows older than a cutoff is naturally
idempotent — running it twice deletes nothing the second time — which is why
retention is expressed that way rather than as "delete the oldest N rows".
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, update

from nexus.core.logging import get_logger
from nexus.db.base import affected_rows, utcnow
from nexus.db.models.audit import AuditEvent
from nexus.db.models.device import Device
from nexus.db.models.event import SecurityEvent
from nexus.services.audit import AuditAction, AuditActor, AuditService
from nexus.services.jobs import JobQueue
from nexus.workers.registry import JobContext, registry

logger = get_logger(__name__)

# Deletes run in batches so one statement never holds locks for minutes or
# builds a write-ahead-log record the size of the table. A long DELETE also
# blocks autovacuum from cleaning up behind it, which makes the next one worse.
DELETE_BATCH_SIZE = 5_000
MAX_BATCHES_PER_RUN = 40


@registry.register(
    "retention.prune_events", description="Delete security events past the retention window."
)
async def prune_events(context: JobContext) -> dict[str, Any]:
    """Enforce the event retention window.

    Idempotent: the cutoff is computed from the clock, so a second run finds
    nothing left to delete.

    Batched and bounded: if a long outage leaves millions of rows to remove,
    this run deletes what it can and the next scheduled run continues. A job
    that tries to delete everything in one transaction on a large table is a
    job that times out and never makes progress.
    """
    days = int(context.payload.get("days") or context.settings.event_retention_days)
    cutoff = utcnow() - timedelta(days=days)
    deleted = 0

    for _ in range(MAX_BATCHES_PER_RUN):
        if context.cancelled:
            break
        # Delete by primary key from a bounded subquery: PostgreSQL has no
        # DELETE ... LIMIT, and this form lets the planner use the timestamp
        # index instead of scanning.
        victims = (
            select(SecurityEvent.id)
            .where(SecurityEvent.occurred_at < cutoff)
            .order_by(SecurityEvent.id)
            .limit(DELETE_BATCH_SIZE)
            .scalar_subquery()
        )
        result = await context.session.execute(
            delete(SecurityEvent).where(SecurityEvent.id.in_(victims))
        )
        batch = affected_rows(result)
        deleted += batch
        if batch < DELETE_BATCH_SIZE:
            break

    if deleted:
        logger.info("events_pruned", extra={"deleted": deleted, "retention_days": days})
    return {"deleted": deleted, "retention_days": days, "cutoff": cutoff.isoformat()}


@registry.register(
    "retention.prune_audit", description="Delete audit entries past the retention window."
)
async def prune_audit(context: JobContext) -> dict[str, Any]:
    """Enforce audit retention, and record that it happened.

    Two things make this different from event pruning:

    * It prunes only from the **oldest end**, preserving the hash chain's
      property that everything from the retention watermark forward links
      correctly. Deleting from the middle would break verification.
    * The pruning is itself audited. An audit log that can be trimmed without
      a trace is an audit log with a hole in it.
    """
    days = int(context.payload.get("days") or context.settings.audit_retention_days)
    cutoff = utcnow() - timedelta(days=days)

    oldest_kept = await context.session.execute(
        select(func.min(AuditEvent.id)).where(AuditEvent.occurred_at >= cutoff)
    )
    boundary = oldest_kept.scalar_one_or_none()
    if boundary is None:
        # Everything is older than the cutoff. Deleting the entire log would
        # destroy the chain and every record of why. Refuse and say so.
        return {"deleted": 0, "reason": "refused: would delete the entire audit log"}

    result = await context.session.execute(delete(AuditEvent).where(AuditEvent.id < boundary))
    deleted = affected_rows(result)

    if deleted:
        await AuditService(context.session, context.settings).record(
            AuditAction.AUDIT_PRUNED,
            actor=AuditActor.system("retention"),
            reason=f"Audit retention window is {days} days.",
            details={"deleted": deleted, "cutoff": cutoff.isoformat(), "kept_from_id": boundary},
        )
        logger.info("audit_pruned", extra={"deleted": deleted, "retention_days": days})

    return {"deleted": deleted, "retention_days": days, "kept_from_id": boundary}


@registry.register("jobs.purge_finished", description="Remove old completed jobs.")
async def purge_finished_jobs(context: JobContext) -> dict[str, Any]:
    """Delete succeeded and cancelled jobs older than a week.

    DEAD and FAILED jobs are deliberately kept: they are the ones a human still
    needs to look at.
    """
    days = int(context.payload.get("days", 7))
    deleted = await JobQueue(context.session).purge_finished(older_than_days=days)
    return {"deleted": deleted, "older_than_days": days}


@registry.register("devices.reconcile_state", description="Mark unseen devices as idle.")
async def reconcile_device_state(context: JobContext) -> dict[str, Any]:
    """Move devices that have not been seen recently from ACTIVE to IDLE.

    Presentation only — no evidence is changed, and the transition is fully
    reversible the moment the device is observed again.

    Quarantined devices are excluded. Their state is a response decision made
    by a person, and no background job gets to undo it.
    """
    hours = int(context.payload.get("idle_after_hours", 24))
    cutoff = utcnow() - timedelta(hours=hours)

    result = await context.session.execute(
        update(Device)
        .where(Device.state == "ACTIVE", Device.last_seen_at < cutoff)
        .values(state="IDLE")
        .execution_options(synchronize_session=False)
    )
    idled = affected_rows(result)
    if idled:
        logger.info("devices_marked_idle", extra={"count": idled, "idle_after_hours": hours})
    return {"marked_idle": idled, "idle_after_hours": hours}
