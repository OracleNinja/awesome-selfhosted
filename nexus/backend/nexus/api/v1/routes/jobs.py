"""Background job visibility and control."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.middleware import client_ip
from nexus.api.schemas.common import responses
from nexus.api.schemas.monitoring import JobPage, JobSummary, QueueStatsResponse
from nexus.api.security import AuditDep, actor_from, require
from nexus.core.errors import Conflict, NotFound
from nexus.core.rbac import Permission
from nexus.db.base import utcnow
from nexus.db.models.job import Job
from nexus.services.audit import AuditAction
from nexus.services.auth import AuthenticatedIdentity
from nexus.services.jobs import JobQueue
from nexus.workers.registry import registry

router = APIRouter(prefix="/jobs", tags=["jobs"])

JobReaderDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.JOBS_READ))]
JobManagerDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.JOBS_MANAGE))]


@router.get(
    "",
    response_model=JobPage,
    summary="List background jobs",
    description="Newest first. Filter by status to find what failed or what is stuck.",
    responses=responses(401, 403),
)
async def list_jobs(
    identity: JobReaderDep,
    session: SessionDep,
    status: Annotated[str | None, Query(max_length=16)] = None,
    queue: Annotated[str | None, Query(max_length=32)] = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> JobPage:
    conditions: list[Any] = []
    if status:
        conditions.append(Job.status == status)
    if queue:
        conditions.append(Job.queue == queue)
    if kind:
        conditions.append(Job.kind == kind)

    total = (
        await session.execute(select(func.count()).select_from(Job).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(Job)
                .where(*conditions)
                .order_by(Job.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return JobPage(items=[JobSummary.model_validate(row) for row in rows], total=total)


@router.get(
    "/stats",
    response_model=QueueStatsResponse,
    summary="Queue depth and health",
    description=(
        "The number that matters is `oldest_pending_age_seconds`, not the "
        "pending count: a large queue draining quickly is a busy system, while "
        "a small queue with an hour-old job is a stopped one.\n\n"
        "`known_kinds` lists the job kinds this build can run. A job whose kind "
        "is absent will be marked DEAD rather than retried — that is what a "
        "rollback past a job type looks like."
    ),
    responses=responses(401, 403),
)
async def queue_stats(identity: JobReaderDep, session: SessionDep) -> QueueStatsResponse:
    stats = await JobQueue(session).stats()
    return QueueStatsResponse(
        pending=stats.pending,
        running=stats.running,
        dead=stats.dead,
        oldest_pending_age_seconds=stats.oldest_pending_age_seconds,
        healthy=stats.is_healthy,
        known_kinds=registry.known_kinds(),
    )


@router.get(
    "/{job_id}",
    response_model=JobSummary,
    summary="Get one job",
    responses=responses(401, 403, 404),
)
async def get_job(job_id: uuid.UUID, identity: JobReaderDep, session: SessionDep) -> JobSummary:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFound("No such job.")
    return JobSummary.model_validate(job)


@router.post(
    "/{job_id}/cancel",
    response_model=JobSummary,
    summary="Cancel a job",
    description=(
        "A pending job is cancelled immediately. A running job is *asked* to "
        "stop and does so at its handler's next checkpoint — cancellation is "
        "cooperative, because killing a worker mid-transaction is not "
        "cancellation, it is half-applied state with nobody aware."
    ),
    responses=responses(401, 403, 404),
)
async def cancel_job(
    job_id: uuid.UUID,
    request: Request,
    identity: JobManagerDep,
    session: SessionDep,
    context: ContextDep,
    audit: AuditDep,
) -> JobSummary:
    job = await JobQueue(session).request_cancel(job_id)
    await audit.record(
        AuditAction.SETTING_CHANGED,
        actor=actor_from(identity),
        target_type="job",
        target_id=str(job.id),
        target_label=job.kind,
        reason="Job cancellation requested.",
        source_ip=client_ip(request, context.settings),
        details={"status": job.status, "cancel_requested": job.cancel_requested},
    )
    return JobSummary.model_validate(job)


@router.post(
    "/{job_id}/retry",
    response_model=JobSummary,
    summary="Retry a dead or failed job",
    description=(
        "Resets the attempt counter and makes the job runnable again. Use it "
        "after fixing whatever caused the failure.\n\n"
        "Only DEAD, FAILED and CANCELLED jobs can be retried: retrying a "
        "running job would run it twice concurrently."
    ),
    responses=responses(401, 403, 404, 409),
)
async def retry_job(
    job_id: uuid.UUID,
    request: Request,
    identity: JobManagerDep,
    session: SessionDep,
    context: ContextDep,
    audit: AuditDep,
) -> JobSummary:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFound("No such job.")
    if job.status not in ("DEAD", "FAILED", "CANCELLED"):
        raise Conflict(
            f"A job in status {job.status} cannot be retried.",
            details={"retryable_statuses": ["DEAD", "FAILED", "CANCELLED"]},
        )

    job.status = "PENDING"
    job.attempts = 0
    job.run_after = utcnow()
    job.started_at = None
    job.finished_at = None
    job.cancel_requested = False
    job.locked_by = None
    job.locked_at = None

    await audit.record(
        AuditAction.SETTING_CHANGED,
        actor=actor_from(identity),
        target_type="job",
        target_id=str(job.id),
        target_label=job.kind,
        reason="Job retried by operator.",
        source_ip=client_ip(request, context.settings),
        details={"previous_error": (job.last_error or "")[:200]},
    )
    return JobSummary.model_validate(job)
