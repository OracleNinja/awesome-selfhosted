"""Job queue tests.

The properties that matter are the ones that are hard to get right: two workers
never run the same job, a dead worker's jobs come back, retries back off, and a
burst of identical requests collapses to one job.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from nexus.core.errors import NotFound
from nexus.db.base import utcnow
from nexus.db.models.job import Job
from nexus.db.session import Database
from nexus.services.jobs import BACKOFF_MAX_SECONDS, JobQueue, backoff_delay

pytestmark = pytest.mark.integration


class TestEnqueue:
    async def test_enqueues_a_pending_job(self, session) -> None:
        job = await JobQueue(session).enqueue("test.work", payload={"x": 1})
        assert job is not None
        assert job.status == "PENDING"
        assert job.payload == {"x": 1}
        assert job.attempts == 0

    async def test_dedupe_key_collapses_pending_duplicates(self, session) -> None:
        """A burst of a thousand events must not queue a thousand identical
        'score this device' jobs."""
        queue = JobQueue(session)
        first = await queue.enqueue("device.score", dedupe_key="device:1")
        second = await queue.enqueue("device.score", dedupe_key="device:1")

        assert first is not None
        # None means "already scheduled", which is success, not failure.
        assert second is None

        jobs = (await session.execute(select(Job))).scalars().all()
        assert len(jobs) == 1

    async def test_dedupe_does_not_block_after_completion(self, session) -> None:
        """The unique index is partial on PENDING, so finished work does not
        prevent the next run."""
        queue = JobQueue(session)
        first = await queue.enqueue("device.score", dedupe_key="device:1")
        assert first is not None
        await queue.complete(first)
        await session.flush()

        second = await queue.enqueue("device.score", dedupe_key="device:1")
        assert second is not None

    async def test_dedupe_collision_does_not_poison_the_transaction(self, session) -> None:
        """The savepoint matters: without it the IntegrityError would take down
        the event that triggered the enqueue."""
        queue = JobQueue(session)
        await queue.enqueue("device.score", dedupe_key="device:1")
        await queue.enqueue("device.score", dedupe_key="device:1")
        # The session is still usable.
        assert await queue.enqueue("other.work") is not None

    async def test_delayed_jobs_are_not_immediately_runnable(self, session) -> None:
        queue = JobQueue(session)
        await queue.enqueue("test.later", delay_seconds=300)
        await session.flush()
        assert await queue.claim(worker_id="w1") == []

    async def test_rejects_an_empty_kind(self, session) -> None:
        from nexus.core.errors import ValidationFailed

        with pytest.raises(ValidationFailed):
            await JobQueue(session).enqueue("")


class TestClaim:
    async def test_claim_marks_running_and_increments_attempts(self, session) -> None:
        queue = JobQueue(session)
        await queue.enqueue("test.work")
        await session.flush()

        claimed = await queue.claim(worker_id="worker-1")
        assert len(claimed) == 1
        assert claimed[0].status == "RUNNING"
        assert claimed[0].attempts == 1
        assert claimed[0].locked_by == "worker-1"

    async def test_priority_wins_over_age(self, session) -> None:
        queue = JobQueue(session)
        await queue.enqueue("test.low", priority=0)
        await queue.enqueue("test.high", priority=10)
        await session.flush()

        claimed = await queue.claim(worker_id="w1", batch_size=1)
        assert claimed[0].kind == "test.high"

    async def test_queue_filter(self, session) -> None:
        queue = JobQueue(session)
        await queue.enqueue("test.a", queue="alpha")
        await queue.enqueue("test.b", queue="beta")
        await session.flush()

        claimed = await queue.claim(worker_id="w1", queues=["beta"], batch_size=10)
        assert [job.kind for job in claimed] == ["test.b"]

    async def test_two_workers_never_claim_the_same_job(self, database: Database) -> None:
        """This is what SKIP LOCKED buys. Without it the workers either
        serialise or double-process."""
        async with database.session() as setup:
            queue = JobQueue(setup)
            for index in range(20):
                await queue.enqueue("test.work", payload={"n": index})

        claimed_ids: list[list] = []

        async def worker(name: str) -> None:
            async with database.session() as db_session:
                jobs = await JobQueue(db_session).claim(worker_id=name, batch_size=10)
                claimed_ids.append([job.id for job in jobs])

        await asyncio.gather(*(worker(f"worker-{index}") for index in range(4)))

        flat = [job_id for batch in claimed_ids for job_id in batch]
        assert len(flat) == len(set(flat)), "a job was claimed twice"
        assert len(flat) == 20

    async def test_claiming_an_empty_queue_returns_nothing(self, session) -> None:
        assert await JobQueue(session).claim(worker_id="w1") == []


class TestCompletion:
    async def test_complete_records_the_result(self, session) -> None:
        queue = JobQueue(session)
        job = await queue.enqueue("test.work")
        await session.flush()
        claimed = (await queue.claim(worker_id="w1"))[0]

        await queue.complete(claimed, {"deleted": 12})
        assert claimed.status == "SUCCEEDED"
        assert claimed.result == {"deleted": 12}
        assert claimed.finished_at is not None
        assert claimed.locked_by is None
        assert job is not None

    async def test_failure_schedules_a_retry_in_the_future(self, session) -> None:
        queue = JobQueue(session)
        await queue.enqueue("test.work")
        await session.flush()
        claimed = (await queue.claim(worker_id="w1"))[0]

        await queue.fail(claimed, "boom")
        assert claimed.status == "PENDING"
        assert claimed.run_after > utcnow()
        assert claimed.last_error == "boom"

    async def test_job_dies_after_exhausting_attempts(self, session) -> None:
        """Abandoned work that nobody is told about is the failure a queue
        exists to prevent, so it becomes DEAD and visible."""
        queue = JobQueue(session)
        await queue.enqueue("test.work", max_attempts=2)
        await session.flush()

        for _ in range(2):
            claimed = (await queue.claim(worker_id="w1"))[0]
            await queue.fail(claimed, "still broken")
            # fail() schedules a backoff; skip the wait so the next claim in
            # this loop is eligible immediately.
            claimed.run_after = utcnow() - timedelta(seconds=1)
            await session.flush()

        job = (await session.execute(select(Job))).scalar_one()
        assert job.status == "DEAD"

    async def test_non_retryable_failure_dies_immediately(self, session) -> None:
        """Another attempt will not conjure a missing handler."""
        queue = JobQueue(session)
        await queue.enqueue("test.work", max_attempts=10)
        await session.flush()
        claimed = (await queue.claim(worker_id="w1"))[0]

        await queue.fail(claimed, "no handler registered", retryable=False)
        assert claimed.status == "DEAD"


class TestBackoff:
    def test_delay_grows_with_each_attempt(self) -> None:
        assert backoff_delay(1) < backoff_delay(5)

    def test_delay_is_capped(self) -> None:
        """Without a cap the fifth retry of a long outage lands hours later,
        long after a human fixed it."""
        assert backoff_delay(50) <= BACKOFF_MAX_SECONDS * 1.3

    def test_delay_is_jittered(self) -> None:
        """Without jitter, jobs that failed together retry together and
        reproduce the outage on schedule."""
        delays = {backoff_delay(3) for _ in range(20)}
        assert len(delays) > 1

    def test_delay_is_never_negative(self) -> None:
        assert all(backoff_delay(attempt) > 0 for attempt in range(1, 20))


class TestOrphanReclamation:
    async def test_reclaims_a_job_whose_worker_vanished(self, session) -> None:
        """A worker killed by OOM leaves its jobs RUNNING forever. Nothing else
        notices: the row looks busy."""
        queue = JobQueue(session)
        await queue.enqueue("test.work", timeout_seconds=1)
        await session.flush()
        claimed = (await queue.claim(worker_id="dead-worker"))[0]

        # Simulate the lock ageing past the job's own timeout.
        await session.execute(
            text("UPDATE jobs SET locked_at = now() - interval '10 minutes' WHERE id = :id"),
            {"id": claimed.id},
        )
        session.expunge_all()

        assert await queue.reclaim_orphans(grace_seconds=0) == 1
        job = (await session.execute(select(Job))).scalar_one()
        assert job.status == "PENDING"
        assert job.locked_by is None

    async def test_does_not_reclaim_a_healthy_running_job(self, session) -> None:
        queue = JobQueue(session)
        await queue.enqueue("test.work", timeout_seconds=600)
        await session.flush()
        await queue.claim(worker_id="healthy-worker")

        assert await queue.reclaim_orphans() == 0

    async def test_timeout_is_per_job(self, session) -> None:
        """A 10-second lookup and a 10-minute export must not share a
        deadline."""
        queue = JobQueue(session)
        await queue.enqueue("test.quick", timeout_seconds=5)
        await queue.enqueue("test.slow", timeout_seconds=3600)
        await session.flush()
        await queue.claim(worker_id="w1", batch_size=2)

        await session.execute(text("UPDATE jobs SET locked_at = now() - interval '60 seconds'"))
        session.expunge_all()

        assert await queue.reclaim_orphans(grace_seconds=0) == 1


class TestCancellation:
    async def test_pending_job_is_cancelled_outright(self, session) -> None:
        queue = JobQueue(session)
        job = await queue.enqueue("test.work")
        await session.flush()
        assert job is not None

        cancelled = await queue.request_cancel(job.id)
        assert cancelled.status == "CANCELLED"

    async def test_running_job_is_flagged_not_killed(self, session) -> None:
        """Cooperative: killing a worker mid-transaction is not cancellation,
        it is half-applied state with nobody aware."""
        queue = JobQueue(session)
        job = await queue.enqueue("test.work")
        await session.flush()
        assert job is not None
        await queue.claim(worker_id="w1")

        cancelled = await queue.request_cancel(job.id)
        assert cancelled.status == "RUNNING"
        assert cancelled.cancel_requested is True

    async def test_cancelling_a_finished_job_is_a_no_op(self, session) -> None:
        """A double-click on the UI should not produce a failure."""
        queue = JobQueue(session)
        job = await queue.enqueue("test.work")
        await session.flush()
        assert job is not None
        await queue.complete(job)
        await session.flush()

        result = await queue.request_cancel(job.id)
        assert result.status == "SUCCEEDED"

    async def test_unknown_job_is_not_found(self, session) -> None:
        import uuid

        with pytest.raises(NotFound):
            await JobQueue(session).request_cancel(uuid.uuid4())


class TestStats:
    async def test_counts_by_status(self, session) -> None:
        queue = JobQueue(session)
        for index in range(3):
            await queue.enqueue("test.work", dedupe_key=f"k{index}")
        await session.flush()
        await queue.claim(worker_id="w1", batch_size=1)

        stats = await queue.stats()
        assert stats.pending == 2
        assert stats.running == 1

    async def test_oldest_pending_age_is_reported(self, session) -> None:
        """The number that distinguishes a busy system from a stopped one."""
        queue = JobQueue(session)
        await queue.enqueue("test.work")
        await session.flush()
        await session.execute(text("UPDATE jobs SET run_after = now() - interval '20 minutes'"))

        stats = await queue.stats()
        assert stats.oldest_pending_age_seconds is not None
        assert stats.oldest_pending_age_seconds > 600
        assert stats.is_healthy is False

    async def test_dead_jobs_make_the_queue_unhealthy(self, session) -> None:
        queue = JobQueue(session)
        await queue.enqueue("test.work", max_attempts=1)
        await session.flush()
        claimed = (await queue.claim(worker_id="w1"))[0]
        await queue.fail(claimed, "boom")
        await session.flush()

        stats = await queue.stats()
        assert stats.dead == 1
        assert stats.is_healthy is False

    async def test_empty_queue_is_healthy(self, session) -> None:
        stats = await JobQueue(session).stats()
        assert stats.is_healthy is True
        assert stats.pending == 0


class TestPurge:
    async def test_purges_old_successes_but_keeps_failures(self, session) -> None:
        """DEAD jobs are the ones a human still needs to look at."""
        queue = JobQueue(session)
        succeeded = await queue.enqueue("test.ok", dedupe_key="a")
        dead = await queue.enqueue("test.bad", dedupe_key="b")
        await session.flush()
        assert succeeded is not None and dead is not None

        await queue.complete(succeeded)
        dead.status = "DEAD"
        dead.finished_at = utcnow()
        await session.flush()
        await session.execute(text("UPDATE jobs SET finished_at = now() - interval '30 days'"))

        assert await queue.purge_finished(older_than_days=7) == 1
        remaining = (await session.execute(select(Job.status))).scalars().all()
        assert remaining == ["DEAD"]
