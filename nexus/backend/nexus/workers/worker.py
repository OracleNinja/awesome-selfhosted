"""The worker: claims jobs, runs handlers, records outcomes.

Transaction shape
-----------------
Each attempt gets its own session and its own transaction, and the handler's
work commits **together with** the job's completion::

    BEGIN
      … handler mutates data …
      job.status = SUCCEEDED
    COMMIT

So a crash between "work done" and "job marked done" is impossible: either both
land or neither does, and in the latter case the job is retried. That is the
whole reason to keep the queue in the same database as the data.

Failure takes the opposite path. The handler's transaction is rolled back —
partial work must not survive — and the failure is then recorded in a *second*,
fresh transaction. Recording the failure in the rolled-back transaction would
roll the failure back too, and the job would look like it had never run.

Concurrency
-----------
One asyncio task per in-flight job, bounded by ``worker_concurrency``. These
jobs are I/O-bound (database queries, HTTP lookups), which is exactly what an
event loop is good at: while one waits on a socket, others run. CPU-bound work
inside a handler must be pushed to a thread, or it blocks every other job in
the process — the same rule that sends password hashing to a thread.

Shutdown
--------
On SIGTERM the worker stops claiming, lets in-flight jobs finish within a grace
period, and only then exits. Killing jobs mid-flight would leave their locks to
expire and their work to be redone, turning every deploy into a burst of
duplicate work.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from dataclasses import dataclass, field

from nexus.core.config import Settings
from nexus.core.context import AppContext
from nexus.core.logging import bind_request_id, component_var, get_logger
from nexus.db.models.job import Job
from nexus.services.jobs import JobQueue
from nexus.workers.registry import JobContext, registry
from nexus.workers.scheduler import Scheduler

logger = get_logger(__name__)

# How often a running job refreshes its lock, as a fraction of its timeout.
# Comfortably more often than the reclaimer's threshold, so a healthy worker is
# never mistaken for a dead one.
HEARTBEAT_FRACTION = 0.25
MIN_HEARTBEAT_SECONDS = 5.0

# How often to look for jobs orphaned by a dead worker.
RECLAIM_INTERVAL_SECONDS = 60.0


def default_worker_id() -> str:
    """Identify this worker in ``jobs.locked_by``.

    Hostname plus PID plus a short random suffix: the hostname names the
    machine, the PID distinguishes workers on it, and the suffix survives PID
    reuse after a restart.
    """
    return f"{socket.gethostname()[:32]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


@dataclass
class WorkerStats:
    """Counters for the observability endpoint."""

    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    timed_out: int = 0
    cancelled: int = 0
    reclaimed: int = 0
    last_activity_at: float | None = None
    in_flight: int = 0
    running_kinds: dict[str, int] = field(default_factory=dict)


class Worker:
    """Runs jobs until told to stop."""

    def __init__(
        self,
        context: AppContext,
        *,
        worker_id: str | None = None,
        queues: list[str] | None = None,
        run_scheduler: bool = True,
    ) -> None:
        self._context = context
        self._settings: Settings = context.settings
        self.worker_id = worker_id or default_worker_id()
        self._queues = queues
        # A worker dedicated to one queue may not want to own the recurring
        # schedule as well; tests turn it off so the only jobs in the database
        # are the ones the test put there.
        self._run_scheduler = run_scheduler
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self.stats = WorkerStats()
        self._scheduler: Scheduler | None = None
        self._started = False

    # ------------------------------------------------------------ lifecycle --

    async def run(self) -> None:
        """Main loop. Returns when :meth:`stop` is called and work has drained."""
        component_var.set("worker")
        self._started = True
        logger.info(
            "worker_started",
            extra={
                "worker_id": self.worker_id,
                "concurrency": self._settings.worker_concurrency,
                "queues": self._queues or ["*"],
                "handlers": len(registry),
            },
        )

        reclaimer = asyncio.create_task(self._reclaim_loop())
        # Every worker runs the scheduler; the queue's dedupe index decides
        # which one actually enqueues each due job. No leader election needed.
        scheduler_task: asyncio.Task | None = None
        if self._run_scheduler:
            self._scheduler = Scheduler(self._context)
            scheduler_task = asyncio.create_task(self._scheduler.run())
        try:
            while not self._stop.is_set():
                claimed = await self._claim_and_dispatch()
                if claimed == 0:
                    # Nothing to do: wait for the poll interval, but wake
                    # immediately if asked to stop, so shutdown is not delayed
                    # by a sleep.
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self._settings.worker_poll_interval_seconds,
                        )
        finally:
            reclaimer.cancel()
            if self._scheduler is not None:
                self._scheduler.stop()
            if scheduler_task is not None:
                scheduler_task.cancel()
            for task in (reclaimer, scheduler_task):
                if task is None:
                    continue
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self._drain()
            logger.info(
                "worker_stopped",
                extra={"worker_id": self.worker_id, "succeeded": self.stats.succeeded},
            )

    def stop(self) -> None:
        """Ask the worker to finish. Safe to call from a signal handler."""
        self._stop.set()

    async def _drain(self, grace_seconds: float = 30.0) -> None:
        """Let in-flight jobs finish before exiting."""
        if not self._tasks:
            return
        logger.info("worker_draining", extra={"in_flight": len(self._tasks)})
        _done, pending = await asyncio.wait(self._tasks, timeout=grace_seconds)
        for task in pending:
            # Past the grace period. Cancelling loses the work, but the job's
            # lock expires and another worker will retry it — which is exactly
            # the case at-least-once delivery exists to cover.
            task.cancel()
        if pending:
            logger.warning("worker_drain_timeout", extra={"abandoned": len(pending)})

    # -------------------------------------------------------------- claiming --

    async def _claim_and_dispatch(self) -> int:
        capacity = self._settings.worker_concurrency - len(self._tasks)
        if capacity <= 0:
            # Full. Wait for a slot rather than spinning on the database.
            await asyncio.sleep(self._settings.worker_poll_interval_seconds)
            return 0

        try:
            async with self._context.database.session() as session:
                jobs = await JobQueue(session).claim(
                    worker_id=self.worker_id, queues=self._queues, batch_size=capacity
                )
                # Detach before the session closes so the task can use the data
                # without touching a closed session.
                for job in jobs:
                    session.expunge(job)
        except Exception as exc:
            # A database outage must not kill the worker. It backs off and
            # tries again; when the database returns, so does the worker.
            logger.error(
                "job_claim_failed",
                extra={"error": exc.__class__.__name__, "detail": str(exc)[:200]},
            )
            await asyncio.sleep(min(5.0, self._settings.worker_poll_interval_seconds * 5))
            return 0

        for job in jobs:
            self.stats.claimed += 1
            task = asyncio.create_task(self._execute(job))
            self._tasks.add(task)
            # Discard on completion, so the set is the live in-flight set and
            # does not grow without bound.
            task.add_done_callback(self._tasks.discard)

        self.stats.in_flight = len(self._tasks)
        return len(jobs)

    # ------------------------------------------------------------- execution --

    async def _execute(self, job: Job) -> None:
        """Run one job attempt with its own session, timeout, and heartbeat."""
        # Correlate this attempt's logs with the request that enqueued it, if
        # there was one — that is how you follow a UI click into background work.
        bind_request_id(job.request_id or None)

        handler = registry.resolve(job.kind)
        if handler is None:
            # Not retryable: another attempt will not conjure a handler. This
            # usually means a rollback to a build that predates the job kind.
            await self._record_failure(
                job, f"No handler registered for job kind {job.kind!r}.", retryable=False
            )
            return

        heartbeat_interval = max(MIN_HEARTBEAT_SECONDS, job.timeout_seconds * HEARTBEAT_FRACTION)
        try:
            async with self._context.database.session() as session:
                merged = await session.merge(job)
                context = JobContext(job=merged, session=session, settings=self._settings)
                heartbeat = asyncio.create_task(
                    self._heartbeat(merged, session, heartbeat_interval)
                )
                try:
                    result = await asyncio.wait_for(handler(context), timeout=job.timeout_seconds)
                finally:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat

                if merged.cancel_requested:
                    # The handler stopped early at a checkpoint. Its partial
                    # work is committed with this status, which is why handlers
                    # must only check for cancellation at points where stopping
                    # leaves consistent state.
                    merged.status = "CANCELLED"
                    merged.finished_at = _now()
                    self.stats.cancelled += 1
                else:
                    await JobQueue(session).complete(
                        merged, result if isinstance(result, dict) else None
                    )
                    self.stats.succeeded += 1
                # Handler work and job status commit together here, on exit
                # from the session context manager.

        except TimeoutError:
            self.stats.timed_out += 1
            await self._record_failure(
                job,
                f"Timed out after {job.timeout_seconds}s.",
                retryable=True,
            )
        except asyncio.CancelledError:
            # Shutdown past the grace period. Leave the job RUNNING: its lock
            # will expire and the reclaimer will make it runnable again.
            logger.warning("job_abandoned_on_shutdown", extra={"job_id": str(job.id)})
            raise
        except Exception as exc:
            self.stats.failed += 1
            logger.exception(
                "job_handler_failed",
                extra={"job_id": str(job.id), "kind": job.kind},
            )
            await self._record_failure(job, f"{exc.__class__.__name__}: {exc}")
        finally:
            self.stats.in_flight = max(0, len(self._tasks) - 1)
            self.stats.last_activity_at = asyncio.get_running_loop().time()

    async def _record_failure(self, job: Job, error: str, *, retryable: bool = True) -> None:
        """Record a failure in a fresh transaction.

        The handler's transaction has been rolled back, taking any partial work
        with it. Recording the failure there too would roll the failure back as
        well, and the job would look as though it had never been attempted.
        """
        try:
            async with self._context.database.session() as session:
                merged = await session.merge(job)
                await JobQueue(session).fail(merged, error, retryable=retryable)
        except Exception:
            # The database is gone. The job stays RUNNING; its lock expires and
            # the reclaimer picks it up. Nothing is lost, only delayed.
            logger.exception("job_failure_not_recorded", extra={"job_id": str(job.id)})

    async def _heartbeat(self, job: Job, session, interval: float) -> None:
        """Refresh the job's lock while it runs."""
        while True:
            await asyncio.sleep(interval)
            try:
                job.locked_at = _now()
                # Also picks up a cancellation requested by another process
                # since this attempt started.
                await session.flush()
                await session.refresh(job, ["cancel_requested"])
            except Exception:  # pragma: no cover - transient database issue
                logger.debug("job_heartbeat_failed", extra={"job_id": str(job.id)})

    # ---------------------------------------------------------- reclamation --

    async def _reclaim_loop(self) -> None:
        """Periodically return jobs orphaned by dead workers."""
        while not self._stop.is_set():
            await asyncio.sleep(RECLAIM_INTERVAL_SECONDS)
            try:
                async with self._context.database.session() as session:
                    count = await JobQueue(session).reclaim_orphans()
                    self.stats.reclaimed += count
            except Exception as exc:  # pragma: no cover - transient
                logger.warning("job_reclaim_failed", extra={"error": exc.__class__.__name__})


def _now():
    from nexus.db.base import utcnow

    return utcnow()
