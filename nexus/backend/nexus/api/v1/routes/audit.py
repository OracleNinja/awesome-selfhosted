"""Audit log query and chain verification. Requires `audit:read`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.schemas.admin import AuditEntry, AuditPage, ChainVerificationResponse
from nexus.api.schemas.common import responses
from nexus.api.security import AuditDep, require
from nexus.core.rbac import Permission
from nexus.db.models.audit import AuditEvent
from nexus.services.auth import AuthenticatedIdentity

router = APIRouter(prefix="/audit", tags=["audit"])

AuditReaderDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.AUDIT_READ))]

MAX_PAGE_SIZE = 200


@router.get(
    "",
    response_model=AuditPage,
    summary="Query the audit log",
    description=(
        "Newest first. Paginate with `before_id` (keyset pagination) rather than "
        "an offset: the table only grows, and an offset would both scan and "
        "discard every skipped row and shift page boundaries as new rows arrive."
    ),
    responses=responses(401, 403, 422),
)
async def query_audit(
    identity: AuditReaderDep,
    session: SessionDep,
    before_id: Annotated[
        int | None, Query(description="Return entries older than this id.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    action: Annotated[str | None, Query(max_length=64)] = None,
    actor: Annotated[str | None, Query(max_length=64)] = None,
    outcome: Annotated[str | None, Query(pattern="^(SUCCESS|FAILURE|DENIED)$")] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> AuditPage:
    statement = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit + 1)

    # Every filter is a bound parameter. SQLAlchemy compiles these to
    # placeholders, so a value like `'; DROP TABLE users; --` is compared as a
    # string and never becomes SQL. String concatenation is what makes SQL
    # injection possible, and there is none here.
    if before_id is not None:
        statement = statement.where(AuditEvent.id < before_id)
    if action:
        statement = statement.where(AuditEvent.action == action)
    if actor:
        statement = statement.where(AuditEvent.actor_username == actor.lower())
    if outcome:
        statement = statement.where(AuditEvent.outcome == outcome)
    if since is not None:
        statement = statement.where(AuditEvent.occurred_at >= since)
    if until is not None:
        statement = statement.where(AuditEvent.occurred_at <= until)

    rows = list((await session.execute(statement)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    return AuditPage(
        items=[_to_entry(row) for row in rows],
        next_cursor=rows[-1].id if rows and has_more else None,
        has_more=has_more,
    )


@router.post(
    "/verify",
    response_model=ChainVerificationResponse,
    summary="Verify the audit hash chain",
    description=(
        "Recomputes every entry's hash and reports the first inconsistency.\n\n"
        "A passing result proves the log is internally consistent — no entry has "
        "been edited or removed *without* recomputing everything after it. It "
        "does **not** prove authenticity: anyone with database write access "
        "could rewrite the whole chain. The `external_anchor` field reports "
        "whether an off-database mirror is configured, which is what closes "
        "that gap."
    ),
    responses=responses(401, 403),
)
async def verify_chain(
    identity: AuditReaderDep,
    audit: AuditDep,
    context: ContextDep,
) -> ChainVerificationResponse:
    result = await audit.verify_chain()
    return ChainVerificationResponse(
        ok=result.ok,
        entries_checked=result.entries_checked,
        first_invalid_id=result.first_invalid_id,
        detail=result.detail,
        external_anchor=("CONFIGURED" if context.settings.audit_mirror_path else "NOT_CONFIGURED"),
    )


def _to_entry(row: AuditEvent) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        occurred_at=row.occurred_at,
        actor_username=row.actor_username,
        actor_user_id=row.actor_user_id,
        actor_role=row.actor_role,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        target_label=row.target_label,
        outcome=row.outcome,
        reason=row.reason,
        request_id=row.request_id,
        source_ip=str(row.source_ip) if row.source_ip else None,
        details=row.details or {},
        entry_hash=row.entry_hash,
        prev_hash=row.prev_hash,
    )
