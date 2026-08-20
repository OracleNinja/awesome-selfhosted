"""Detection and risk-scoring jobs.

Both handlers are idempotent, because delivery is at-least-once (see
:mod:`nexus.services.jobs`). They achieve it differently, and both ways are
worth naming:

* ``detection.analyze_pending`` is idempotent because re-analysing an event
  re-derives the same fingerprints, and the database rejects the duplicates. The
  watermark then makes replay cheap rather than merely harmless.
* ``risk.rescore_stale`` is idempotent because the score is *recomputed* from
  current findings rather than adjusted. Running it twice produces the same
  number and writes nothing the second time.

Neither handler commits: the worker commits the handler's work together with the
job's completion, so "the analysis happened" and "the job is done" cannot
disagree.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.db.models.device import Device
from nexus.db.models.finding import ACTIVE_FINDING_STATUSES, Finding
from nexus.detection.config import load_config
from nexus.detection.engine import DEFAULT_BATCH_SIZE, DetectionEngine
from nexus.detection.risk import DECAY_QUANTUM_SECONDS, RiskService
from nexus.workers.registry import JobContext, registry

logger = get_logger(__name__)

# How many devices one rescoring pass will touch. Bounded for the same reason
# every other background job is: a run that tries to do everything is a run that
# times out and never finishes anything.
RESCORE_BATCH_SIZE = 200


@registry.register(
    "detection.analyze_pending",
    description="Run detection rules over security events newer than the watermark.",
)
async def analyze_pending(context: JobContext) -> dict[str, Any]:
    """Analyse the next batch of observations.

    A pass that finds another already running returns ``skipped_locked`` and
    succeeds. That is not a failure: the work is being done, and reporting it as
    an error would make a healthy multi-worker deployment look broken.
    """
    batch_size = int(context.payload.get("batch_size") or DEFAULT_BATCH_SIZE)
    engine = DetectionEngine(context.session, context.settings)
    result = await engine.analyze_pending(batch_size=batch_size)
    return result.as_dict()


@registry.register(
    "risk.rescore_stale",
    description="Recompute risk for devices whose scores have aged.",
)
async def rescore_stale(context: JobContext) -> dict[str, Any]:
    """Let risk decay.

    The recency factor in the scoring model decays with the age of the newest
    active finding, in whole hours. Nothing re-evaluates a device on its own, so
    without this job a device that was HIGH during an incident would stay HIGH
    forever, and the score would stop describing the present.

    Only devices with at least one active finding are considered: a device with
    nothing to decay would produce an identical score, and scanning the whole
    inventory to write nothing is wasted work.
    """
    now = utcnow()
    cutoff = now - timedelta(seconds=DECAY_QUANTUM_SECONDS)
    limit = int(context.payload.get("limit") or RESCORE_BATCH_SIZE)

    loaded = await load_config(context.session)
    service = RiskService(context.session)

    candidates = (
        (
            await context.session.execute(
                select(Device)
                .where(
                    Device.id.in_(
                        select(Finding.device_id).where(
                            Finding.device_id.is_not(None),
                            Finding.status.in_(ACTIVE_FINDING_STATUSES),
                        )
                    ),
                    or_(Device.risk_updated_at.is_(None), Device.risk_updated_at < cutoff),
                )
                .order_by(Device.risk_updated_at.asc().nullsfirst())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    rescored = 0
    changed = 0
    # Locked in id order, matching the analysis pass. Two passes that took the
    # same device locks in different orders could deadlock; a single agreed
    # order removes the possibility rather than relying on them not overlapping.
    for device in sorted(candidates, key=lambda item: str(item.id)):
        if context.cancelled:
            break
        _, did_change = await service.rescore(device, config=loaded.config, now=now)
        rescored += 1
        changed += int(did_change)

    if rescored:
        logger.info("risk_rescored", extra={"devices": rescored, "changed": changed})

    return {
        "devices_considered": rescored,
        "devices_changed": changed,
        "weights_source": loaded.source,
    }


__all__ = ["analyze_pending", "rescore_stale"]
