"""Detection as background work: the handlers, the queue, and the nudge.

These run through the real worker rather than calling handlers directly, so the
thing under test is what production actually does — claim, run, commit the work
and the job together, and be safe when the same job arrives twice.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

import nexus.workers.handlers  # noqa: F401  (registers the handlers)
from nexus.core.context import AppContext
from nexus.db.base import utcnow
from nexus.db.models.device import Device
from nexus.db.models.finding import DetectionState, DeviceRiskAssessment, Finding
from nexus.db.models.job import Job
from nexus.services.jobs import JobQueue
from nexus.workers.worker import Worker
from tests.detection_helpers import discovery, ingest, make_sensor

pytestmark = pytest.mark.integration


async def run_worker_once(context: AppContext, *, deadline: float = 15.0) -> Worker:
    worker = Worker(context, worker_id="detection-test-worker", run_scheduler=False)
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


class TestAnalysisJob:
    async def test_worker_runs_the_analysis_pass(self, context: AppContext) -> None:
        async with context.database.session() as session:
            sensor = await make_sensor(session)
            await ingest(session, sensor, [discovery()])
            await JobQueue(session).enqueue("detection.analyze_pending", queue="analysis")

        await run_worker_once(context)

        async with context.database.session() as session:
            job = (
                await session.execute(select(Job).where(Job.kind == "detection.analyze_pending"))
            ).scalar_one()
            assert job.status == "SUCCEEDED"
            assert job.result is not None
            assert job.result["findings_created"] == 1
            assert job.result["events_examined"] == 1

            findings = (
                await session.execute(select(func.count()).select_from(Finding))
            ).scalar_one()
            assert findings == 1

    async def test_the_same_job_twice_is_harmless(self, context: AppContext) -> None:
        """At-least-once delivery, made concrete."""
        async with context.database.session() as session:
            sensor = await make_sensor(session)
            await ingest(session, sensor, [discovery()])
            await JobQueue(session).enqueue("detection.analyze_pending", queue="analysis")

        await run_worker_once(context)

        async with context.database.session() as session:
            device = (await session.execute(select(Device))).scalar_one()
            score_after_first = device.risk_score
            # Rewind the watermark and enqueue again: the shape of a redelivery.
            state = (await session.execute(select(DetectionState))).scalar_one()
            state.last_event_id = 0
            await JobQueue(session).enqueue("detection.analyze_pending", queue="analysis")

        await run_worker_once(context)

        async with context.database.session() as session:
            findings = (
                await session.execute(select(func.count()).select_from(Finding))
            ).scalar_one()
            assert findings == 1

            device = (await session.execute(select(Device))).scalar_one()
            assert device.risk_score == score_after_first

            assessments = (
                await session.execute(select(func.count()).select_from(DeviceRiskAssessment))
            ).scalar_one()
            assert assessments == 1

    async def test_batch_size_is_respected_and_the_backlog_is_reported(
        self, context: AppContext
    ) -> None:
        async with context.database.session() as session:
            sensor = await make_sensor(session)
            await ingest(
                session,
                sensor,
                [
                    discovery(mac=f"aa:bb:cc:dd:00:{index:02x}", ip=f"192.168.1.{index + 10}")
                    for index in range(10)
                ],
            )
            await JobQueue(session).enqueue(
                "detection.analyze_pending", queue="analysis", payload={"batch_size": 3}
            )

        await run_worker_once(context)

        async with context.database.session() as session:
            job = (
                await session.execute(select(Job).where(Job.kind == "detection.analyze_pending"))
            ).scalar_one()
            assert job.result["events_examined"] == 3
            assert job.result["backlog_remaining"] == 7


class TestIngestionNudge:
    async def test_storing_events_asks_for_analysis(self, context: AppContext) -> None:
        """The request must be part of the same transaction as the events."""
        from nexus.db.models.sensor import Sensor as SensorRow
        from nexus.sensors.manager import SensorManager, SensorRuntime

        manager = SensorManager(context)
        async with context.database.session() as session:
            sensor = await make_sensor(session, name="nudge-sensor")
            sensor_id = sensor.id

        async with context.database.session() as session:
            row = await session.get(SensorRow, sensor_id)
            runtime = SensorRuntime(row_id=sensor_id, name="nudge-sensor", driver="arp_discovery")
            await manager._ingest([discovery()], row, runtime, False)

        async with context.database.session() as session:
            jobs = (
                (await session.execute(select(Job).where(Job.kind == "detection.analyze_pending")))
                .scalars()
                .all()
            )
        assert len(jobs) == 1
        assert jobs[0].queue == "analysis"
        assert jobs[0].dedupe_key == "detection.analyze_pending|ingest"

    async def test_repeated_batches_coalesce_into_one_pending_pass(
        self, context: AppContext
    ) -> None:
        async with context.database.session() as session:
            queue = JobQueue(session)
            first = await queue.enqueue(
                "detection.analyze_pending",
                queue="analysis",
                dedupe_key="detection.analyze_pending|ingest",
            )
            second = await queue.enqueue(
                "detection.analyze_pending",
                queue="analysis",
                dedupe_key="detection.analyze_pending|ingest",
            )
        assert first is not None
        assert second is None  # deduplicated by the partial unique index


class TestRescoreJob:
    async def test_stale_devices_are_reconsidered(self, context: AppContext) -> None:
        async with context.database.session() as session:
            sensor = await make_sensor(session)
            await ingest(session, sensor, [discovery()])
            await JobQueue(session).enqueue("detection.analyze_pending", queue="analysis")

        await run_worker_once(context)

        async with context.database.session() as session:
            # Age the score so the decay job has something to reconsider.
            await session.execute(
                Device.__table__.update().values(risk_updated_at=utcnow() - timedelta(hours=2))
            )
            await JobQueue(session).enqueue("risk.rescore_stale", queue="analysis")

        await run_worker_once(context)

        async with context.database.session() as session:
            job = (
                await session.execute(select(Job).where(Job.kind == "risk.rescore_stale"))
            ).scalar_one()
            assert job.status == "SUCCEEDED"
            assert job.result["devices_considered"] == 1
            assert job.result["weights_source"] == "DEFAULT"

    async def test_devices_without_findings_are_left_alone(self, context: AppContext) -> None:
        async with context.database.session() as session:
            sensor = await make_sensor(session)
            await ingest(session, sensor, [discovery()])
            await JobQueue(session).enqueue("risk.rescore_stale", queue="analysis")

        await run_worker_once(context)

        async with context.database.session() as session:
            job = (
                await session.execute(select(Job).where(Job.kind == "risk.rescore_stale"))
            ).scalar_one()
            assert job.result["devices_considered"] == 0
            device = (await session.execute(select(Device))).scalar_one()
            # Never assessed, so still UNKNOWN rather than a manufactured zero.
            assert device.risk_score is None
            assert device.risk_level == "UNKNOWN"


class TestScheduling:
    def test_detection_is_on_the_default_schedule(self) -> None:
        from nexus.workers.scheduler import DEFAULT_SCHEDULE

        kinds = {entry.kind: entry for entry in DEFAULT_SCHEDULE}
        assert "detection.analyze_pending" in kinds
        assert kinds["detection.analyze_pending"].interval_seconds == 60
        assert kinds["detection.analyze_pending"].queue == "analysis"
        assert "risk.rescore_stale" in kinds

    def test_handlers_are_registered(self) -> None:
        from nexus.workers.registry import registry

        known = registry.known_kinds()
        assert "detection.analyze_pending" in known
        assert "risk.rescore_stale" in known
