"""Abuse protection for the expensive endpoints.

Digitizing, tracing, mockups and export are CPU-bound and take seconds. Without
a limit, one client — or one runaway retry loop — can saturate every worker and
take the service down for everyone. Plan quotas do not help here: they count
*designs*, and re-digitizing the same design is unlimited on every tier.

Scope, stated plainly: this is an **in-process** sliding window. It protects a
single worker and is the right default for a single-instance or small
deployment. Behind multiple replicas each process keeps its own counters, so
the effective limit is `N x limit`. For a real multi-replica deployment,
back :func:`_hits` with Redis; the call sites do not change.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status

from app.core.deps import Context, CurrentContext

# bucket -> (max requests, window seconds)
LIMITS: dict[str, tuple[int, int]] = {
    # Heavy CPU: tracing, stitch generation, rendering, packaging.
    "digitize": (30, 60),
    "upload": (20, 60),
    "export": (20, 60),
    "mockup": (30, 60),
    # Calls an external vision API on our bill.
    "ai_analysis": (15, 60),
    # Cheap but unauthenticated-adjacent and easy to hammer.
    "preview": (60, 60),
}

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _check(bucket: str, key: str) -> tuple[bool, int]:
    """Returns (allowed, seconds_until_retry)."""
    limit, window = LIMITS.get(bucket, (60, 60))
    now = time.monotonic()
    cutoff = now - window

    with _lock:
        hits = _hits[(bucket, key)]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= limit:
            return (False, max(1, int(hits[0] + window - now) + 1))
        hits.append(now)

        # Opportunistic cleanup so idle keys do not accumulate forever.
        if len(_hits) > 5000:
            for stale_key in [k for k, v in _hits.items() if not v or v[-1] < cutoff]:
                _hits.pop(stale_key, None)

    return (True, 0)


def rate_limit(bucket: str):
    """FastAPI dependency factory. Limits per workspace, not per user, because
    the cost lands on the workspace's plan and a team shares one budget."""

    def dependency(context: CurrentContext) -> Context:
        allowed, retry_after = _check(bucket, str(context.org_id))
        if not allowed:
            limit, window = LIMITS.get(bucket, (60, 60))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many {bucket.replace('_', ' ')} requests — the limit is "
                       f"{limit} per {window} seconds. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        return context

    return dependency


def reset() -> None:
    """Test hook: clear all counters."""
    with _lock:
        _hits.clear()
