"""Periodic work, without a scheduler daemon.

The problem with recurring jobs in a multi-process system is agreement: if
three workers each decide "it is time to run retention", retention runs three
times. The usual answers are a leader election, a distributed lock, or a single
designated scheduler process — all of which add a failure mode (what happens
when the leader dies?).

The approach here uses the queue's own uniqueness instead. For each schedule
entry, the current time is floored into a bucket::

    bucket = floor(now / interval)
    dedupe_key = f"{kind}|{bucket}"

Every worker computes the same key for the same interval and tries to enqueue
it. The partial unique index on ``(dedupe_key) WHERE status = 'PENDING'`` means
exactly one insert succeeds and the rest are silently discarded. No leader, no
lock, no scheduler process — and if every worker but one dies, the survivor
still schedules everything.

The trade: firing is aligned to interval boundaries rather than to wall-clock
times, so this expresses "every six hours", not "at 03:00 daily". For cron-style
scheduling, the same trick works with a bucket derived from the cron
expression's next fire time; that is the documented upgrade path and is not
needed by anything yet.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.services.jobs import JobQueue

logger = get_logger(__name__)


@dataclass(frozen=True)
class ScheduleEntry:
    kind: str
    interval_seconds: int
    queue: str = "maintenance"
    priority: int = -10  # below interactive work: housekeeping can wait
    timeout_seconds: int = 300
    max_attempts: int = 3


# The built-in schedule. Intervals are deliberately unaligned with each other
# so the jobs do not all fire in the same minute.
DEFAULT_SCHEDULE: tuple[ScheduleEntry, ...] = (
    ScheduleEntry("retention.prune_events", interval_seconds=6 * 3600),
    ScheduleEntry("retention.prune_audit", interval_seconds=24 * 3600),
    ScheduleEntry("jobs.purge_finished", interval_seconds=12 * 3600),
    ScheduleEntry("devices.reconcile_state", interval_seconds=900),
    # Detection runs on the "analysis" queue at normal priority: it is the only
    # background work an operator is actually waiting on, so it must not sit
    # behind a retention sweep. Ingestion also nudges it directly (see
    # nexus.sensors.manager), and this entry is the floor that guarantees a pass
    # happens even when no sensor is producing.
    ScheduleEntry(
        "detection.analyze_pending",
        interval_seconds=60,
        queue="analysis",
        priority=0,
        timeout_seconds=120,
    ),
    ScheduleEntry(
        "risk.rescore_stale",
        interval_seconds=3600,
        queue="analysis",
        priority=-5,
        timeout_seconds=300,
    ),
)

# How often to evaluate the schedule. Must be well below the shortest interval,
# or a bucket could pass unnoticed between ticks.
TICK_SECONDS = 60.0


class Scheduler:
    """Enqueues recurring jobs. Safe to run in every worker process."""

    def __init__(self, context, schedule: tuple[ScheduleEntry, ...] = DEFAULT_SCHEDULE) -> None:
        self._context = context
        self._schedule = schedule
        self._stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("scheduler_started", extra={"entries": len(self._schedule)})
        try:
            while not self._stop.is_set():
                with contextlib.suppress(Exception):
                    await self.tick()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
        finally:
            logger.info("scheduler_stopped")

    def stop(self) -> None:
        self._stop.set()

    async def tick(self) -> int:
        """Enqueue anything due. Returns how many jobs this process created.

        A return of zero is the normal case in a multi-worker deployment: some
        other worker won the race, which is exactly the intended behaviour.
        """
        now = utcnow().timestamp()
        created = 0
        async with self._context.database.session() as session:
            queue = JobQueue(session)
            for entry in self._schedule:
                bucket = int(now // entry.interval_seconds)
                job = await queue.enqueue(
                    entry.kind,
                    queue=entry.queue,
                    priority=entry.priority,
                    timeout_seconds=entry.timeout_seconds,
                    max_attempts=entry.max_attempts,
                    dedupe_key=f"{entry.kind}|{bucket}",
                )
                if job is not None:
                    created += 1
        if created:
            logger.debug("scheduler_enqueued", extra={"count": created})
        return created
