"""In-process rate limiting.

What this is for
----------------
Slowing down automated abuse: credential stuffing against the login endpoint,
a script hammering an expensive query. It is a *speed bump*, not the security
control — the security control for password guessing is the durable per-account
lockout in :mod:`nexus.services.auth`, which lives in the database.

The distinction matters, so it is stated plainly rather than left implied:

* This limiter's state is **per process** and **in memory**. Two worker
  processes each allow the configured budget, and a restart clears it.
* An attacker with many source addresses is not meaningfully slowed.

Making it durable would mean a counter in PostgreSQL or Redis, which adds a
write (or a dependency) to the hot path of every request in order to improve a
control that is not load-bearing. The account lockout is what actually bounds
password guessing, and it is durable precisely because it must be.

Algorithm: sliding window log
-----------------------------
Each key keeps the timestamps of its recent hits; anything older than the
window is dropped, and the request is allowed if what remains is under the
limit. Compared to the alternatives:

* A **fixed window** ("100 per minute, reset on the minute") allows 200
  requests across a window boundary — the classic burst bug.
* A **token bucket** is O(1) in memory and is what you would use at high
  volume, but it makes "how many did they make in the last minute?" harder to
  answer, and NEXUS wants that number for its audit trail.

At NEXUS's request rates the memory cost of keeping timestamps is trivial, and
the behaviour is exactly what an operator would predict from the setting.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class SlidingWindowRateLimiter:
    """Counts hits per key over a rolling time window."""

    def __init__(self, *, limit: int, window_seconds: float = 60.0, max_keys: int = 10_000) -> None:
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        """Record a hit against ``key`` and decide whether to allow it."""
        current = now if now is not None else time.monotonic()
        window_start = current - self._window

        hits = self._hits[key]
        while hits and hits[0] <= window_start:
            hits.popleft()

        if len(hits) >= self._limit:
            retry_after = max(1, int(hits[0] + self._window - current) + 1)
            return RateLimitDecision(allowed=False, remaining=0, retry_after_seconds=retry_after)

        hits.append(current)
        self._evict_if_needed(current)
        return RateLimitDecision(
            allowed=True, remaining=self._limit - len(hits), retry_after_seconds=0
        )

    def _evict_if_needed(self, now: float) -> None:
        """Bound memory use.

        Without this, an attacker rotating source addresses turns the limiter
        itself into a memory-exhaustion vector — the defence becomes the
        vulnerability. Eviction drops keys whose windows have fully expired,
        and if that is not enough, the oldest keys go.
        """
        if len(self._hits) <= self._max_keys:
            return
        window_start = now - self._window
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= window_start]
        for key in stale:
            del self._hits[key]
        if len(self._hits) > self._max_keys:
            for key in list(self._hits)[: len(self._hits) - self._max_keys]:
                del self._hits[key]

    def reset(self, key: str | None = None) -> None:
        """Clear state for one key, or all of it. Used on successful login."""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)
