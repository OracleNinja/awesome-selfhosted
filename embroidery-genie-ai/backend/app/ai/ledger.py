"""The AI usage ledger.

Writing to this table is the last thing that happens on every path through the
gateway — success, failure, cache hit, budget block, kill switch. That is what
makes the ledger a meter rather than a log: if a row is missing, the call did
not happen.

Deliberately *not* stored: prompt text, artwork bytes, model output, customer
names. Cost control needs counts, money and outcomes. Keeping the artwork hash
(not the artwork) is enough to explain a cache hit without retaining the file.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import pricing
from app.models import AICallEvent

log = logging.getLogger(__name__)

SUCCESS = "success"
FAILURE = "failure"
CACHE_HIT = "served_from_cache"
BLOCKED = "blocked"
SKIPPED = "skipped"


@dataclass
class Usage:
    """Token counts for one attempt."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: False when these are our pre-flight estimate rather than the provider's
    #: own report — a timed-out call still burned input tokens, and pretending
    #: otherwise would make the meter optimistic exactly when it matters.
    measured: bool = False

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass
class CallRecord:
    operation: str
    outcome: str
    organization_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    provider: str | None = None
    model: str | None = None
    tier: str | None = None
    attempt: int = 1
    reason: str | None = None
    cache_hit: bool = False
    usage: Usage = field(default_factory=Usage)
    latency_ms: int | None = None
    artwork_hash: str | None = None
    request_id: str | None = None
    meta: dict = field(default_factory=dict)


def record(db: Session, call: CallRecord) -> AICallEvent:
    """Append one attempt to the ledger and return the row.

    The row is flushed but not committed: it joins whatever transaction the
    request is already in, so a rolled-back request does not leave a phantom
    charge.
    """
    cost, basis = (None, None)
    if call.model:
        cost, basis = pricing.estimate_cost(
            call.model,
            call.usage.input_tokens,
            call.usage.output_tokens,
            call.usage.cache_read_tokens,
            call.usage.cache_write_tokens,
        )

    meta = dict(call.meta)
    if basis:
        meta["rate_basis"] = basis
    elif call.model and call.usage.total:
        meta["unpriced_model"] = call.model

    event = AICallEvent(
        organization_id=call.organization_id,
        user_id=call.user_id,
        operation=call.operation,
        provider=call.provider,
        model=call.model,
        tier=call.tier,
        attempt=call.attempt,
        outcome=call.outcome,
        reason=call.reason,
        cache_hit=call.cache_hit,
        input_tokens=call.usage.input_tokens,
        output_tokens=call.usage.output_tokens,
        cache_read_tokens=call.usage.cache_read_tokens,
        cache_write_tokens=call.usage.cache_write_tokens,
        tokens_measured=call.usage.measured,
        estimated_cost_usd=cost,
        latency_ms=call.latency_ms,
        artwork_hash=call.artwork_hash,
        request_id=call.request_id,
        meta=meta,
    )
    db.add(event)
    db.flush()
    return event


# ------------------------------------------------------------------ rollups
_BILLABLE = (SUCCESS, FAILURE)


def _spend_window(
    db: Session,
    since: datetime,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> tuple[int, float]:
    """(tokens, estimated dollars) charged since ``since``.

    Only outcomes that reached a provider count. A cache hit costs nothing and a
    blocked call never left the building; charging for either would make the
    budget punish the very behaviour it is trying to encourage.
    """
    query = select(
        func.coalesce(
            func.sum(
                AICallEvent.input_tokens
                + AICallEvent.output_tokens
                + AICallEvent.cache_read_tokens
                + AICallEvent.cache_write_tokens
            ),
            0,
        ),
        func.coalesce(func.sum(AICallEvent.estimated_cost_usd), 0),
    ).where(AICallEvent.occurred_at >= since, AICallEvent.outcome.in_(_BILLABLE))

    if organization_id is not None:
        query = query.where(AICallEvent.organization_id == organization_id)
    if user_id is not None:
        query = query.where(AICallEvent.user_id == user_id)

    tokens, cost = db.execute(query).one()
    return int(tokens or 0), float(cost or 0.0)


def start_of_day(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def start_of_month(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def user_day(db: Session, user_id: uuid.UUID | None) -> tuple[int, float]:
    if user_id is None:
        return 0, 0.0
    return _spend_window(db, start_of_day(), user_id=user_id)


def tenant_day(db: Session, organization_id: uuid.UUID | None) -> tuple[int, float]:
    if organization_id is None:
        return 0, 0.0
    return _spend_window(db, start_of_day(), organization_id=organization_id)


def tenant_month(db: Session, organization_id: uuid.UUID | None) -> tuple[int, float]:
    if organization_id is None:
        return 0, 0.0
    return _spend_window(db, start_of_month(), organization_id=organization_id)


def global_month(db: Session) -> tuple[int, float]:
    return _spend_window(db, start_of_month())


def purge_older_than(db: Session, days: int) -> int:
    """Retention hook. The ledger is small, but it is still usage data."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(AICallEvent).filter(AICallEvent.occurred_at < cutoff).delete()
    return int(rows or 0)
