"""Background jobs: a work queue built on PostgreSQL.

Why a queue at all
------------------
An HTTP request must return in milliseconds. Enriching a device with threat
intelligence, running a detection sweep over an hour of events, or exporting
evidence takes seconds to minutes. Doing that work inside the request holds a
connection, a worker slot, and the user's browser hostage — and if the process
restarts mid-way, the work is simply lost with nobody aware.

A queue turns "do this now, while you wait" into "record that this must
happen". The record is durable, so a crash loses nothing; it is retryable, so a
transient failure is not fatal; and it is observable, so an operator can see
what is stuck.

Why PostgreSQL instead of Redis/Celery/RabbitMQ
-----------------------------------------------
The decisive property is **transactional enqueue**. When ingestion stores an
event and enqueues "score this device", both are in the same transaction: they
commit together or not at all. With an external broker, the event can commit
and the job publish can fail, leaving work nobody will ever do — or the reverse,
a job referencing a row that was rolled back. Handling that means outbox tables
and idempotency keys, which is more machinery than the broker saved.

The cost is throughput: this design handles thousands of jobs a minute, not
millions. For one home network, that ceiling is irrelevant, and one fewer
service to run, back up, and monitor is a real operational win.

How the claim works
-------------------
    UPDATE jobs SET status = 'RUNNING', locked_by = :worker
    WHERE id IN (
        SELECT id FROM jobs
        WHERE status = 'PENDING' AND run_after <= now()
        ORDER BY priority DESC, run_after
        FOR UPDATE SKIP LOCKED
        LIMIT :batch
    )
    RETURNING *;

``FOR UPDATE`` locks the rows the subquery selected. ``SKIP LOCKED`` is what
makes it a queue rather than a bottleneck: a second worker running the same
statement *skips* rows already locked by the first instead of waiting behind
them, so N workers pull N disjoint batches with no coordination and no
possibility of two workers running the same job. Without ``SKIP LOCKED`` the
workers serialise; without ``FOR UPDATE`` they double-process.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

JOB_STATUSES = (
    "PENDING",  # waiting to be claimed
    "RUNNING",  # claimed by a worker
    "SUCCEEDED",
    "FAILED",  # failed, will be retried
    "DEAD",  # out of attempts; needs a human
    "CANCELLED",
)

# Terminal states: a job in one of these is never picked up again.
TERMINAL_STATUSES = ("SUCCEEDED", "DEAD", "CANCELLED")


class Job(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "jobs"

    # Named lanes so a flood of one kind of work cannot starve another: a
    # backlog of threat-intel lookups must not delay retention or detection.
    queue: Mapped[str] = mapped_column(String(32), nullable=False, server_default="default")

    # Which handler runs it, e.g. "device.score", "intel.lookup",
    # "retention.prune". Resolved against a registry at execution time.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)

    # Arguments. JSONB rather than a pickle: an unpickle is arbitrary code
    # execution, so a queue that deserialises pickles turns any database write
    # into remote code execution on a worker. JSON cannot execute anything.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")

    # Higher runs first. Enough range for "urgent response action" to jump a
    # backlog of routine enrichment.
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))

    # When it becomes eligible. Also the backoff mechanism: a failed job is
    # rescheduled by pushing this into the future.
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))

    # Wall-clock budget for one attempt. A job without one can hang forever
    # holding a worker slot, and the queue quietly stops moving.
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("120")
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Which worker holds it, and since when. Together these let a supervisor
    # reclaim jobs orphaned by a worker that died without releasing them —
    # a RUNNING row whose lock is older than its timeout is not running.
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Cooperative cancellation: the handler checks this between units of work.
    # Killing a worker mid-transaction is not cancellation, it is corruption.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Who or what enqueued it, for the jobs screen and for audit correlation.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Deduplication key. A unique partial index (below) makes "score this
    # device" collapse to one pending job however many events ask for it,
    # which is what stops a burst of traffic from queueing ten thousand
    # identical jobs.
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'DEAD', 'CANCELLED')",
            name="status_valid",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("timeout_seconds > 0", name="timeout_positive"),
        # THE index for the claim query. Partial — it covers only PENDING rows,
        # so it stays small no matter how much completed history accumulates,
        # and its column order matches the query's ORDER BY exactly so the
        # planner can walk it instead of sorting.
        Index(
            "ix_jobs_claim",
            "queue",
            priority.desc(),
            "run_after",
            postgresql_where=text("status = 'PENDING'"),
        ),
        # Finds jobs orphaned by a dead worker.
        Index(
            "ix_jobs_running_locked_at",
            "locked_at",
            postgresql_where=text("status = 'RUNNING'"),
        ),
        # One pending job per dedupe key. Partial, so a completed job with the
        # same key does not block the next one from being enqueued.
        Index(
            "uq_jobs_dedupe_key_pending",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'PENDING' AND dedupe_key IS NOT NULL"),
        ),
        # The jobs screen: recent history per queue.
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Job {self.kind} {self.status} attempts={self.attempts}>"
