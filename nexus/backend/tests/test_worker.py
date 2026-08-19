"""Worker tests: execution, timeouts, failure handling, and the scheduler.

The interesting property is the transaction shape — a handler's work and the
job's completion commit together, and a failed handler's work is rolled back
while the *failure record* survives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import select

# Importing the handlers package is what registers the built-in job kinds.
# Without it a worker claims `retention.prune_events` and immediately marks it
# DEAD with "no handler registered" — which is exactly what a rollback past a
# job type looks like, and exactly what these tests must not silently hit.
import nexus.workers.handlers  # noqa: F401
from nexus.core.context import AppContext
from nexus.db.models.device import Device
from nexus.db.models.job import Job
from nexus.services.jobs import JobQueue
from nexus.workers.registry import JobContext, registry
from nexus.workers.scheduler import ScheduleEntry, Scheduler
from nexus.workers.worker import Worker

pytestmark = pytest.mark.integration


@pytest.fixture
def clean_registry() -> Iterator[None]:
    """Register test handlers without leaking them into other tests."""
    saved = dict(registry._handlers)
    saved_descriptions = dict(registry._descriptions)
    yield
    registry._handlers = saved
    registry._descriptions = saved_descriptions


async def run_worker_once(context: AppContext, *, deadline: float = 10.0) -> Worker:
    """Run a worker until the queue is empty, then stop it."""
    worker = Worker(context, worker_id="test-worker", run_scheduler=False)
    task = asyncio.create_task(worker.run())

    async def drained() -> None:
        while True:
            async with context.database.session() as session:
                stats = await JobQueue(session).stats()
            if stats.pending == 0 and stats.running == 0:
                return
            await asyncio.sleep(0.05)

    try:
        await asyncio.wait_for(drained(), timeout=deadline)
    finally:
        worker.stop()
        await asyncio.wait_for(task, timeout=deadline)
    return worker


class TestExecution:
    async def test_runs_a_handler_and_records_the_result(
        self, context: AppContext, clean_registry: None
    ) -> None:
        @registry.register("test.echo")
        async def echo(job_context: JobContext) -> dict:
            return {"echoed": job_context.payload.get("value")}

        async with context.database.session() as session:
            await JobQueue(session).enqueue("test.echo", payload={"value": 42})

        worker = await run_worker_once(context)

        async with context.database.session() as session:
            job = (await session.execute(select(Job))).scalar_one()
        assert job.status == "SUCCEEDED"
        assert job.result == {"echoed": 42}
        assert worker.stats.succeeded == 1

    async def test_handler_work_and_completion_commit_together(
        self, context: AppContext, clean_registry: None
    ) -> None:
        """The reason the queue lives in the same database as the data."""

        @registry.register("test.creates_device")
        async def create_device(job_context: JobContext) -> dict:
            job_context.session.add(
                Device(mac_address="aa:bb:cc:00:00:01", ip_address="192.168.1.5")
            )
            return {"created": True}

        async with context.database.session() as session:
            await JobQueue(session).enqueue("test.creates_device")

        await run_worker_once(context)

        async with context.database.session() as session:
            devices = (await session.execute(select(Device))).scalars().all()
            job = (await session.execute(select(Job))).scalar_one()
        assert len(devices) == 1
        assert job.status == "SUCCEEDED"

    async def test_failed_handler_work_is_rolled_back(
        self, context: AppContext, clean_registry: None
    ) -> None:
        """Partial work must not survive a failure — but the failure record
        must, which is why it is written in a second transaction."""

        @registry.register("test.half_then_fail")
        async def half_then_fail(job_context: JobContext) -> dict:
            job_context.session.add(
                Device(mac_address="aa:bb:cc:00:00:02", ip_address="192.168.1.6")
            )
            await job_context.session.flush()
            raise RuntimeError("boom")

        async with context.database.session() as session:
            await JobQueue(session).enqueue("test.half_then_fail", max_attempts=1)

        await run_worker_once(context)

        async with context.database.session() as session:
            devices = (await session.execute(select(Device))).scalars().all()
            job = (await session.execute(select(Job))).scalar_one()

        assert devices == []  # rolled back
        assert job.status == "DEAD"  # but the failure was recorded
        assert "boom" in (job.last_error or "")

    async def test_unknown_kind_dies_without_retrying(
        self, context: AppContext, clean_registry: None
    ) -> None:
        """A rollback past a job type should not retry forever."""
        async with context.database.session() as session:
            await JobQueue(session).enqueue("test.no_such_handler", max_attempts=5)

        await run_worker_once(context)

        async with context.database.session() as session:
            job = (await session.execute(select(Job))).scalar_one()
        assert job.status == "DEAD"
        assert "No handler registered" in (job.last_error or "")
        assert job.attempts == 1  # not five

    async def test_timeout_is_enforced(self, context: AppContext, clean_registry: None) -> None:
        """A job without an enforced budget can hang forever holding a worker
        slot, and the queue quietly stops moving."""

        @registry.register("test.hangs")
        async def hangs(job_context: JobContext) -> dict:
            await asyncio.sleep(30)
            return {}

        async with context.database.session() as session:
            await JobQueue(session).enqueue("test.hangs", timeout_seconds=1, max_attempts=1)

        worker = await run_worker_once(context, deadline=20)

        async with context.database.session() as session:
            job = (await session.execute(select(Job))).scalar_one()
        assert job.status == "DEAD"
        assert "Timed out" in (job.last_error or "")
        assert worker.stats.timed_out == 1

    async def test_one_failing_job_does_not_stop_the_others(
        self, context: AppContext, clean_registry: None
    ) -> None:
        @registry.register("test.ok")
        async def ok(job_context: JobContext) -> dict:
            return {"ok": True}

        @registry.register("test.bad")
        async def bad(job_context: JobContext) -> dict:
            raise ValueError("nope")

        async with context.database.session() as session:
            queue = JobQueue(session)
            await queue.enqueue("test.bad", max_attempts=1, dedupe_key="bad")
            for index in range(3):
                await queue.enqueue("test.ok", dedupe_key=f"ok-{index}")

        await run_worker_once(context)

        async with context.database.session() as session:
            statuses = (await session.execute(select(Job.status))).scalars().all()
        assert sorted(statuses) == ["DEAD", "SUCCEEDED", "SUCCEEDED", "SUCCEEDED"]

    async def test_handlers_run_concurrently(
        self, context: AppContext, clean_registry: None
    ) -> None:
        """I/O-bound work is what an event loop is for: four 200ms jobs should
        not take 800ms."""
        started = asyncio.Event()
        running = 0
        peak = 0

        @registry.register("test.slow_io")
        async def slow_io(job_context: JobContext) -> dict:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            started.set()
            await asyncio.sleep(0.2)
            running -= 1
            return {}

        async with context.database.session() as session:
            queue = JobQueue(session)
            for index in range(4):
                await queue.enqueue("test.slow_io", dedupe_key=f"slow-{index}")

        await run_worker_once(context)
        assert peak > 1


class TestCancellation:
    async def test_a_cancelled_job_stops_at_its_checkpoint(
        self, context: AppContext, clean_registry: None
    ) -> None:
        """Cooperative cancellation: the handler decides where stopping leaves
        consistent state."""
        checkpoints = 0

        @registry.register("test.checkpointed")
        async def checkpointed(job_context: JobContext) -> dict:
            nonlocal checkpoints
            for _ in range(50):
                await job_context.session.refresh(job_context.job, ["cancel_requested"])
                if job_context.cancelled:
                    return {"stopped_early": True}
                checkpoints += 1
                await asyncio.sleep(0.05)
            return {"completed": True}

        async with context.database.session() as session:
            job = await JobQueue(session).enqueue("test.checkpointed", timeout_seconds=30)
            assert job is not None
            job_id = job.id

        worker = Worker(context, worker_id="cancel-test", run_scheduler=False)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)

        async with context.database.session() as session:
            await JobQueue(session).request_cancel(job_id)

        async def wait_for_finish() -> None:
            while True:
                async with context.database.session() as session:
                    row = await session.get(Job, job_id)
                    if row is not None and row.status in ("CANCELLED", "SUCCEEDED", "DEAD"):
                        return
                await asyncio.sleep(0.05)

        try:
            await asyncio.wait_for(wait_for_finish(), timeout=15)
        finally:
            worker.stop()
            await asyncio.wait_for(task, timeout=15)

        async with context.database.session() as session:
            job_row = await session.get(Job, job_id)
        assert job_row is not None
        assert job_row.status == "CANCELLED"
        assert checkpoints < 50


class TestScheduler:
    async def test_enqueues_due_work(self, context: AppContext) -> None:
        scheduler = Scheduler(
            context, schedule=(ScheduleEntry("devices.reconcile_state", interval_seconds=900),)
        )
        assert await scheduler.tick() == 1

        async with context.database.session() as session:
            jobs = (await session.execute(select(Job))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].kind == "devices.reconcile_state"

    async def test_a_second_tick_in_the_same_window_enqueues_nothing(
        self, context: AppContext
    ) -> None:
        """This is what replaces leader election: every worker tries, and the
        queue's partial unique index decides."""
        scheduler = Scheduler(
            context, schedule=(ScheduleEntry("devices.reconcile_state", interval_seconds=900),)
        )
        await scheduler.tick()
        assert await scheduler.tick() == 0

    async def test_concurrent_schedulers_produce_one_job(self, context: AppContext) -> None:
        schedule = (ScheduleEntry("devices.reconcile_state", interval_seconds=900),)
        schedulers = [Scheduler(context, schedule=schedule) for _ in range(5)]

        results = await asyncio.gather(
            *(scheduler.tick() for scheduler in schedulers), return_exceptions=True
        )
        created = sum(result for result in results if isinstance(result, int))

        async with context.database.session() as session:
            jobs = (await session.execute(select(Job))).scalars().all()
        assert len(jobs) == 1
        assert created == 1


class TestMaintenanceHandlers:
    async def test_retention_prunes_old_events(self, context: AppContext) -> None:
        from sqlalchemy import text

        from nexus.db.models.event import SecurityEvent
        from nexus.db.models.sensor import Sensor as SensorRow
        from nexus.sensors.base import RawObservation
        from nexus.services.ingest import IngestionService

        async with context.database.session() as session:
            sensor = SensorRow(name="s", driver="arp_discovery", config={})
            session.add(sensor)
            await session.flush()
            await IngestionService(session).ingest(
                [
                    RawObservation(
                        kind="device.seen",
                        category="DISCOVERY",
                        summary=f"event {index}",
                        source_ip="192.168.1.10",
                        uid_seed=f"old-{index}",
                    )
                    for index in range(5)
                ],
                sensor,
                driver_is_simulation=False,
            )
            # Age them past the retention window.
            await session.execute(
                text("UPDATE security_events SET occurred_at = now() - interval '200 days'")
            )
            await JobQueue(session).enqueue("retention.prune_events", payload={"days": 90})

        await run_worker_once(context)

        async with context.database.session() as session:
            remaining = (await session.execute(select(SecurityEvent))).scalars().all()
            job = (
                await session.execute(select(Job).where(Job.kind == "retention.prune_events"))
            ).scalar_one()
        assert job.status == "SUCCEEDED", job.last_error
        assert remaining == []
        assert job.result["deleted"] == 5

    async def test_retention_refuses_to_empty_the_audit_log(self, context: AppContext) -> None:
        """Deleting the entire chain would destroy both the history and every
        record of why."""
        from sqlalchemy import text

        from nexus.db.models.audit import AuditEvent
        from nexus.services.audit import AuditAction, AuditActor, AuditService

        async with context.database.session() as session:
            await AuditService(session, context.settings).record(
                AuditAction.SYSTEM_STARTED, actor=AuditActor.system()
            )
            await session.execute(
                text("UPDATE audit_events SET occurred_at = now() - interval '9999 days'")
            )
            await JobQueue(session).enqueue("retention.prune_audit", payload={"days": 30})

        await run_worker_once(context)

        async with context.database.session() as session:
            entries = (await session.execute(select(AuditEvent))).scalars().all()
        # The original entry survives; only the refusal is recorded as a result.
        assert len(entries) == 1

    async def test_devices_go_idle_but_quarantine_is_untouched(self, context: AppContext) -> None:
        from sqlalchemy import text

        async with context.database.session() as session:
            session.add(Device(mac_address="aa:bb:cc:00:00:10", ip_address="192.168.1.20"))
            session.add(
                Device(
                    mac_address="aa:bb:cc:00:00:11",
                    ip_address="192.168.1.21",
                    state="QUARANTINED",
                )
            )
            await session.flush()
            await session.execute(
                text("UPDATE devices SET last_seen_at = now() - interval '5 days'")
            )
            await JobQueue(session).enqueue("devices.reconcile_state")

        await run_worker_once(context)

        async with context.database.session() as session:
            states = {
                device.mac_address: device.state
                for device in (await session.execute(select(Device))).scalars().all()
            }
        assert states["aa:bb:cc:00:00:10"] == "IDLE"
        # No background job undoes a response action.
        assert states["aa:bb:cc:00:00:11"] == "QUARANTINED"
