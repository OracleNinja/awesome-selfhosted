"""The job queue: enqueue, claim, complete, retry, reclaim.

See :mod:`nexus.db.models.job` for why the queue lives in PostgreSQL. This
module is the operations on it.

Delivery semantics: **at least once**
-------------------------------------
A worker can claim a job, do the work, and die before recording success. The
job's lock then expires and another worker runs it again. The alternative —
exactly once — is not available: committing "the work is done" and the work
itself are two different systems, and no protocol makes two systems commit
atomically without a distributed transaction.

So handlers must be **idempotent**: running one twice must leave the same state
as running it once. Concretely, that means upserting rather than inserting,
using deterministic ids derived from inputs, and checking whether the effect
already exists before causing it again. Every handler in this codebase says in
its docstring how it achieves that.

Retry policy
------------
Exponential backoff with jitter. Exponential because a dependency that just
failed is unlikely to be fixed a second later, and hammering it delays its
recovery. Jitter because without it, a hundred jobs that failed together retry
together — a thundering herd that reproduces the outage on schedule.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, extract, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.errors import NotFound, ValidationFailed
from nexus.core.logging import get_logger, request_id_var
from nexus.db.base import affected_rows, utcnow
from nexus.db.models.job import Job

logger = get_logger(__name__)

DEFAULT_QUEUE = "default"

# Backoff: 5s, 10s, 20s, 40s … capped. The cap matters — without one, the
# fifth retry of a long-lived outage lands hours later, long after a human
# fixed it.
BACKOFF_BASE_SECONDS = 5
BACKOFF_MAX_SECONDS = 600
# ±25%, so jobs that failed together do not retry in lockstep.
BACKOFF_JITTER = 0.25

MAX_ERROR_LENGTH = 4000


@dataclass(frozen=True)
class QueueStats:
    """Queue depth by status — the numbers that say whether work is moving."""

    pending: int
    running: int
    failed: int
    dead: int
    oldest_pending_age_seconds: float | None

    @property
    def is_healthy(self) -> bool:
        """A rough health signal for the status endpoint.

        Dead jobs mean work was abandoned and needs a human. A pending job
        older than fifteen minutes means workers are down, stuck, or
        outnumbered — either way, nobody is processing the queue.
        """
        if self.dead > 0:
            return False
        stalled = bool(self.oldest_pending_age_seconds and self.oldest_pending_age_seconds > 900)
        return not stalled


class JobQueue:
    """Operations on the job table, scoped to one database session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------- enqueue --

    async def enqueue(
        self,
        kind: str,
        *,
        payload: dict[str, Any] | None = None,
        queue: str = DEFAULT_QUEUE,
        priority: int = 0,
        delay_seconds: float = 0,
        max_attempts: int = 5,
        timeout_seconds: int = 120,
        dedupe_key: str | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Job | None:
        """Add work to the queue, inside the caller's transaction.

        Returns ``None`` when ``dedupe_key`` matched a job that is already
        pending — the work is already scheduled, so this is success, not
        failure. That is what stops a burst of a thousand events from queueing a
        thousand identical "score this device" jobs.

        The transactional part matters: the job commits with whatever data
        caused it, so there is no window where the data exists and the work was
        never scheduled, or the reverse.
        """
        if not kind or len(kind) > 64:
            raise ValidationFailed("Job kind must be 1-64 characters.")

        job = Job(
            queue=queue[:32],
            kind=kind,
            payload=payload or {},
            priority=max(-32_768, min(32_767, priority)),
            run_after=utcnow() + timedelta(seconds=max(0.0, delay_seconds)),
            max_attempts=max(1, max_attempts),
            timeout_seconds=max(1, timeout_seconds),
            dedupe_key=dedupe_key[:128] if dedupe_key else None,
            created_by_user_id=created_by_user_id,
            request_id=request_id_var.get(),
            status="PENDING",
        )
        try:
            # A savepoint, so a dedupe collision does not poison the caller's
            # whole transaction. Without it the IntegrityError leaves the
            # session unusable and takes down the event that triggered the
            # enqueue. The `add` happens *inside* the savepoint so the rollback
            # unwinds it, and the object is expunged afterwards so a later
            # autoflush cannot resurrect the doomed INSERT.
            async with self._session.begin_nested():
                self._session.add(job)
                await self._session.flush()
        except IntegrityError:
            if job in self._session:
                self._session.expunge(job)
            logger.debug("job_deduplicated", extra={"kind": kind, "dedupe_key": dedupe_key})
            return None

        logger.info(
            "job_enqueued",
            extra={"job_id": str(job.id), "kind": kind, "queue": queue, "priority": priority},
        )
        return job

    # --------------------------------------------------------------- claim --

    async def claim(
        self, *, worker_id: str, queues: list[str] | None = None, batch_size: int = 1
    ) -> list[Job]:
        """Atomically take up to ``batch_size`` runnable jobs.

        One statement does the whole thing: select eligible rows with
        ``FOR UPDATE SKIP LOCKED``, mark them RUNNING, and return them. Doing it
        as select-then-update in two statements would leave a window in which
        two workers both saw the same PENDING row.

        ``SKIP LOCKED`` is the crucial part — a second worker skips rows the
        first has locked instead of blocking behind them, so workers scale
        without coordinating.
        """
        now = utcnow()
        eligible: Select = (
            select(Job.id)
            .where(Job.status == "PENDING", Job.run_after <= now)
            .order_by(Job.priority.desc(), Job.run_after.asc())
            .limit(max(1, batch_size))
            .with_for_update(skip_locked=True)
        )
        if queues:
            eligible = eligible.where(Job.queue.in_(queues))

        statement = (
            update(Job)
            .where(Job.id.in_(eligible.scalar_subquery()))
            .values(
                status="RUNNING",
                locked_by=worker_id[:64],
                locked_at=now,
                started_at=now,
                attempts=Job.attempts + 1,
            )
            .returning(Job)
            # synchronize_session=False: the UPDATE has already done the work
            # in the database, so there is nothing to synchronise by re-running
            # the criteria in Python.
            #
            # populate_existing=True: without it, a row already in this
            # session's identity map keeps its *pre-claim* attribute values
            # even though RETURNING carries the new ones — so a caller that
            # claims and then fails a job in the same session would read a
            # stale `attempts` and never exhaust its retries.
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = await self._session.execute(statement)
        jobs = list(result.scalars().all())
        if jobs:
            logger.debug("jobs_claimed", extra={"worker_id": worker_id, "count": len(jobs)})
        return jobs

    # ------------------------------------------------------------ complete --

    async def complete(self, job: Job, result: dict[str, Any] | None = None) -> None:
        job.status = "SUCCEEDED"
        job.finished_at = utcnow()
        job.result = result or {}
        job.last_error = None
        job.locked_by = None
        job.locked_at = None
        logger.info(
            "job_succeeded",
            extra={
                "job_id": str(job.id),
                "kind": job.kind,
                "attempts": job.attempts,
                "duration_ms": _duration_ms(job),
            },
        )

    async def fail(self, job: Job, error: str, *, retryable: bool = True) -> None:
        """Record a failure, and either schedule a retry or bury the job.

        A job that has exhausted its attempts becomes DEAD rather than being
        deleted or retried forever. Dead jobs are visible on the jobs screen and
        make the queue unhealthy, because abandoned work that nobody is told
        about is the failure mode a queue is supposed to prevent.
        """
        job.last_error = error[:MAX_ERROR_LENGTH]
        job.locked_by = None
        job.locked_at = None

        exhausted = job.attempts >= job.max_attempts
        if not retryable or exhausted:
            job.status = "DEAD"
            job.finished_at = utcnow()
            logger.error(
                "job_dead",
                extra={
                    "job_id": str(job.id),
                    "kind": job.kind,
                    "attempts": job.attempts,
                    "reason": "not_retryable" if not retryable else "attempts_exhausted",
                    "error": job.last_error,
                },
            )
            return

        delay = backoff_delay(job.attempts)
        job.status = "PENDING"
        job.run_after = utcnow() + timedelta(seconds=delay)
        job.started_at = None
        logger.warning(
            "job_retry_scheduled",
            extra={
                "job_id": str(job.id),
                "kind": job.kind,
                "attempt": job.attempts,
                "retry_in_seconds": round(delay, 1),
                "error": job.last_error,
            },
        )

    async def heartbeat(self, job: Job) -> None:
        """Extend a running job's lock.

        Orphan reclamation assumes a RUNNING job whose lock is older than its
        timeout has lost its worker. A long job that never refreshes its lock
        would therefore be stolen and run twice concurrently. A heartbeat is
        how a healthy worker says "still mine".
        """
        job.locked_at = utcnow()

    # -------------------------------------------------------------- cancel --

    async def request_cancel(self, job_id: uuid.UUID) -> Job:
        """Ask a job to stop.

        Cooperative: a PENDING job is cancelled outright, while a RUNNING job
        is flagged and stops at its handler's next checkpoint. Killing a worker
        mid-transaction is not cancellation — it is how you get half-applied
        state with nobody aware.
        """
        # populate_existing: `claim` updates rows with a bulk UPDATE and
        # synchronize_session=False, which leaves any copy already in this
        # session's identity map showing the *old* status. Without a forced
        # refresh, cancelling a job that this same session just claimed would
        # read a stale PENDING and cancel it outright instead of flagging it.
        job = await self._session.get(Job, job_id, populate_existing=True)
        if job is None:
            raise NotFound("No such job.")

        if job.status == "PENDING":
            job.status = "CANCELLED"
            job.finished_at = utcnow()
        elif job.status == "RUNNING":
            job.cancel_requested = True
        # Terminal states are left alone: cancelling a finished job is a no-op,
        # not an error, so a double-click on the UI does not produce a failure.
        return job

    # --------------------------------------------------------- maintenance --

    async def reclaim_orphans(self, *, grace_seconds: int = 30) -> int:
        """Return jobs whose worker vanished to the pending pool.

        A worker killed by OOM, a container eviction, or a power cut leaves its
        jobs RUNNING forever. Nothing else notices: the row looks busy. This
        finds rows whose lock is older than their own timeout plus a grace
        period and makes them runnable again.

        This is also where at-least-once delivery becomes visible: a job that
        actually completed but died before recording it will run a second time.
        Handlers must be idempotent.
        """
        now = utcnow()
        statement = (
            update(Job)
            .where(
                Job.status == "RUNNING",
                Job.locked_at.isnot(None),
                # Per-job timeout, not a global one: a 10-second lookup and a
                # 10-minute export must not share a deadline. Computed in SQL
                # against the *database's* clock, so a worker with a skewed
                # system clock cannot reclaim jobs that are still running.
                extract("epoch", func.now() - Job.locked_at) > Job.timeout_seconds + grace_seconds,
            )
            .values(
                status="PENDING",
                locked_by=None,
                locked_at=None,
                started_at=None,
                run_after=now,
                last_error="Worker stopped responding; job reclaimed.",
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        count = affected_rows(result)
        if count:
            logger.warning("jobs_reclaimed", extra={"count": count})
        return count

    async def purge_finished(self, *, older_than_days: int = 7) -> int:
        """Delete old terminal jobs.

        Completed jobs are kept briefly so an operator can see what ran, then
        removed. Keeping them forever turns the jobs table into the largest in
        the database and slows the claim query's index maintenance.
        """
        from sqlalchemy import delete

        cutoff = utcnow() - timedelta(days=older_than_days)
        result = await self._session.execute(
            delete(Job).where(Job.status.in_(["SUCCEEDED", "CANCELLED"]), Job.finished_at < cutoff)
        )
        return affected_rows(result)

    # --------------------------------------------------------------- reads --

    async def get(self, job_id: uuid.UUID) -> Job:
        job = await self._session.get(Job, job_id)
        if job is None:
            raise NotFound("No such job.")
        return job

    async def stats(self, *, queue: str | None = None) -> QueueStats:
        """Queue depth and the age of the oldest waiting job.

        The age is the number that matters: a large pending count with a young
        oldest job is a busy system, while a small count with an hour-old job is
        a stuck one.
        """
        statement = select(Job.status, func.count()).group_by(Job.status)
        if queue:
            statement = statement.where(Job.queue == queue)
        counts: dict[str, int] = {
            row[0]: row[1] for row in (await self._session.execute(statement)).all()
        }

        oldest_statement = select(func.min(Job.run_after)).where(Job.status == "PENDING")
        if queue:
            oldest_statement = oldest_statement.where(Job.queue == queue)
        oldest: datetime | None = (
            await self._session.execute(oldest_statement)
        ).scalar_one_or_none()

        age = None
        if oldest is not None:
            age = max(0.0, (utcnow() - oldest).total_seconds())

        return QueueStats(
            pending=counts.get("PENDING", 0),
            running=counts.get("RUNNING", 0),
            failed=counts.get("FAILED", 0),
            dead=counts.get("DEAD", 0),
            oldest_pending_age_seconds=age,
        )


def backoff_delay(attempt: int, *, base: float = BACKOFF_BASE_SECONDS) -> float:
    """Exponential backoff with jitter, capped.

    ``attempt`` is 1-based (the first failure is attempt 1), so the first retry
    waits ``base`` seconds rather than ``base/2``.
    """
    raw = min(base * (2 ** max(0, attempt - 1)), BACKOFF_MAX_SECONDS)
    # Full-spread jitter around the target, never negative.
    jitter = raw * BACKOFF_JITTER
    return float(max(0.5, raw + random.uniform(-jitter, jitter)))  # noqa: S311 - not cryptographic


def _duration_ms(job: Job) -> float | None:
    if job.started_at is None or job.finished_at is None:
        return None
    return round((job.finished_at - job.started_at).total_seconds() * 1000, 2)
