"""Tenant-scoped cache for model answers.

The same logo gets uploaded, re-analysed, duplicated into a new order and
re-analysed again. Paying a provider four times for four identical answers is
the easiest money in the product to stop spending.

**Tenant isolation.** Every lookup filters on ``organization_id`` and the table
is unique on ``(organization_id, cache_key)``. Two workspaces that upload the
same file each keep their own entry and each pay once. This costs a little
efficiency and buys the property that matters more: artwork belongs to the
customer who uploaded it, and no cross-tenant read is possible even by accident.

**Key composition.** The key mixes the artwork hash with everything that could
change the answer — operation, prompt version, model, and the shape of what was
actually sent. Edit the prompt and old entries stop matching instead of quietly
answering under the new version.

Unattributed calls (no organization) are never cached. There is no tenant to
scope the entry to, and a shared bucket is exactly the leak this design avoids.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AIResultCache

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheHit:
    result: dict
    created_at: datetime
    hit_count: int
    model: str


def build_key(
    *,
    operation: str,
    artwork_hash: str,
    prompt_version: str,
    model: str,
    request_shape: dict | None = None,
) -> str:
    payload = json.dumps(
        {
            "operation": operation,
            "artwork": artwork_hash,
            "prompt": prompt_version,
            "model": model,
            "shape": request_shape or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def lookup(db: Session, organization_id: uuid.UUID | None, cache_key: str) -> CacheHit | None:
    if not settings.ai_cache_enabled or organization_id is None:
        return None

    row = db.scalar(
        select(AIResultCache).where(
            # Both predicates, always. The tenant filter is not an optimisation.
            AIResultCache.organization_id == organization_id,
            AIResultCache.cache_key == cache_key,
        )
    )
    if row is None:
        return None

    if row.expires_at <= datetime.now(timezone.utc):
        db.delete(row)
        db.flush()
        return None

    row.hit_count += 1
    row.last_hit_at = datetime.now(timezone.utc)
    db.flush()
    return CacheHit(
        result=dict(row.result), created_at=row.created_at, hit_count=row.hit_count, model=row.model
    )


def store(
    db: Session,
    organization_id: uuid.UUID | None,
    *,
    cache_key: str,
    operation: str,
    artwork_hash: str,
    prompt_version: str,
    model: str,
    result: dict,
) -> None:
    if not settings.ai_cache_enabled or organization_id is None or not result:
        return

    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.ai_cache_ttl_days)
    existing = db.scalar(
        select(AIResultCache).where(
            AIResultCache.organization_id == organization_id,
            AIResultCache.cache_key == cache_key,
        )
    )
    if existing is not None:
        existing.result = result
        existing.model = model
        existing.expires_at = expires_at
        db.flush()
        return

    entry = AIResultCache(
        organization_id=organization_id,
        cache_key=cache_key,
        operation=operation,
        artwork_hash=artwork_hash,
        prompt_version=prompt_version,
        model=model,
        result=result,
        expires_at=expires_at,
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        # Two concurrent analyses of the same artwork raced. Both answers are
        # equivalent; the loser just drops its write.
        db.rollback()
        log.debug("Cache write raced for %s; keeping the existing entry.", cache_key[:12])


def previously_analyzed(
    db: Session, organization_id: uuid.UUID | None, artwork_hash: str
) -> bool:
    """Has this workspace seen this exact artwork before, under any settings?

    Used for duplicate reporting. Deliberately scoped to the tenant: whether
    *someone else* uploaded the same file is not this workspace's business.
    """
    if organization_id is None:
        return False
    return db.scalar(
        select(AIResultCache.id).where(
            AIResultCache.organization_id == organization_id,
            AIResultCache.artwork_hash == artwork_hash,
        ).limit(1)
    ) is not None


def purge_expired(db: Session) -> int:
    result = db.execute(
        delete(AIResultCache).where(AIResultCache.expires_at <= datetime.now(timezone.utc))
    )
    return int(result.rowcount or 0)


def invalidate_organization(db: Session, organization_id: uuid.UUID) -> int:
    result = db.execute(
        delete(AIResultCache).where(AIResultCache.organization_id == organization_id)
    )
    return int(result.rowcount or 0)
