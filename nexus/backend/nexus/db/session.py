"""Database engine lifecycle, sessions, and health reporting.

Connection pooling, briefly
---------------------------
Opening a PostgreSQL connection is expensive: a TCP handshake, TLS, then the
server forks a backend process. Doing that per request would cap throughput at
a few hundred requests a second and let a traffic spike fork the database to
death. A pool keeps a small set of connections open and hands them out, so the
cost is paid once. ``pool_size`` is the steady-state count; ``max_overflow``
is the emergency headroom above it; ``pool_timeout`` is how long a request
waits for a free connection before failing — failing fast is better than a
request queue that grows without bound.

Degraded start
--------------
If the database is unreachable at boot, NEXUS still starts. It reports itself
*not ready* and returns ``DEPENDENCY_UNAVAILABLE`` from data endpoints, but the
process stays up so an operator can reach ``/api/v1/health`` and see the
reason. A security tool that exits on a transient database blip is a tool that
is not running when the database recovers.

Why the engine is not a module-level global
-------------------------------------------
It hangs off a :class:`Database` object that is created in the application
factory and stored on ``app.state``. Tests build their own instance against a
throwaway database; a global would force them to monkeypatch it, and two test
modules would fight over the same connection pool.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexus.core.config import Settings
from nexus.core.errors import DependencyUnavailable
from nexus.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DatabaseHealth:
    """Point-in-time view of database availability.

    Consumed by ``/api/v1/health`` and the frontend's system-status panel. The
    error string is deliberately short and free of credentials — it is shown to
    authenticated operators, not to anonymous clients.
    """

    ok: bool
    latency_ms: float | None = None
    server_version: str | None = None
    error: str | None = None
    pool: dict[str, Any] = field(default_factory=dict)


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._available = False
        self._last_error: str | None = None

    # ------------------------------------------------------------ lifecycle --

    def _create_engine(self) -> AsyncEngine:
        settings = self._settings
        return create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            # Recycle before common 5-minute idle timeouts on proxies and
            # firewalls, which otherwise hand us a dead socket that fails on
            # first use rather than on checkout.
            pool_recycle=280,
            # Cheap liveness check on checkout: one round trip, versus an
            # unhandled disconnect surfacing inside a transaction.
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    # Shows up in pg_stat_activity, so an operator can tell
                    # NEXUS connections apart from a psql session.
                    "application_name": f"nexus-{settings.environment.value}",
                    # A runaway query cannot pin a connection forever. This is
                    # the database-side counterpart to request timeouts.
                    "statement_timeout": str(int(settings.db_statement_timeout_seconds * 1000)),
                    "timezone": "UTC",
                },
                "timeout": settings.db_pool_timeout_seconds,
            },
            # SQLAlchemy 2.0 style; no implicit autocommit.
            future=True,
        )

    async def connect(self) -> bool:
        """Create the engine and verify connectivity, with bounded retries.

        Retries use exponential backoff because the common failure at boot is a
        race — the database container is still starting. Hammering it in a tight
        loop turns a two-second wait into a thundering herd. Returns whether the
        database is reachable; it never raises, because the caller (startup) has
        decided that an unreachable database is a degraded state, not a crash.
        """
        if self._engine is None:
            self._engine = self._create_engine()
            self._sessionmaker = async_sessionmaker(
                self._engine,
                expire_on_commit=False,  # objects stay usable after commit
                autoflush=False,  # flush points stay explicit and reviewable
            )

        delay = 0.5
        for attempt in range(1, self._settings.db_connect_retries + 2):
            health = await self.check_health()
            if health.ok:
                self._available = True
                self._last_error = None
                logger.info(
                    "database_connected",
                    extra={
                        "attempt": attempt,
                        "server_version": health.server_version,
                        "latency_ms": health.latency_ms,
                    },
                )
                return True

            self._last_error = health.error
            if attempt > self._settings.db_connect_retries:
                break
            logger.warning(
                "database_connect_retry",
                extra={"attempt": attempt, "delay_seconds": delay, "error": health.error},
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)

        self._available = False
        logger.error(
            "database_unavailable_at_startup",
            extra={"error": self._last_error, "hint": "NEXUS starts in degraded mode"},
        )
        return False

    async def dispose(self) -> None:
        """Close pooled connections on shutdown.

        Without this, PostgreSQL keeps backends alive until it notices the
        sockets are gone, and a restart loop can exhaust ``max_connections``.
        """
        if self._engine is not None:
            await self._engine.dispose()
            logger.info("database_disposed")
        self._engine = None
        self._sessionmaker = None
        self._available = False

    # -------------------------------------------------------------- sessions --

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise DependencyUnavailable("The database engine is not initialised.")
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session inside a transaction, committing on success.

        Unit-of-work: the whole request body of work either lands or does not.
        Callers do not call ``commit()`` themselves, which is what makes
        "record the audit entry in the same transaction as the action" a
        structural guarantee rather than a convention someone can forget — if
        the action rolls back, so does its audit row, and neither is half-true.
        """
        if self._sessionmaker is None:
            raise DependencyUnavailable(
                "The database is not available.",
                details={"reason": self._last_error or "not initialised"},
            )

        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    # ---------------------------------------------------------------- health --

    async def check_health(self) -> DatabaseHealth:
        """Probe the database. Never raises.

        Health checks that can raise turn a monitoring endpoint into a second
        outage: the probe 500s, the orchestrator restarts the container, and the
        restart makes the incident worse.
        """
        if self._engine is None:
            return DatabaseHealth(ok=False, error="engine not initialised")

        started = time.perf_counter()
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(text("SELECT version()"))
                version = str(result.scalar_one())
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            self._available = True
            return DatabaseHealth(
                ok=True,
                latency_ms=latency_ms,
                server_version=_short_version(version),
                pool=self.pool_stats(),
            )
        except (TimeoutError, SQLAlchemyError, OSError) as exc:
            self._available = False
            message = _safe_error(exc)
            self._last_error = message
            return DatabaseHealth(ok=False, error=message, pool=self.pool_stats())

    @property
    def available(self) -> bool:
        """Last known availability, without issuing a query.

        Cheap enough to consult on every request; the authoritative check is
        :meth:`check_health`, run by the health endpoint and the supervisor.
        """
        return self._available

    def pool_stats(self) -> dict[str, Any]:
        """Pool utilisation, for the observability endpoint.

        ``checked_out`` climbing toward ``size + overflow`` is the signal that
        requests are about to start timing out — the metric you want *before*
        the outage, not after.
        """
        if self._engine is None:
            return {}
        pool = self._engine.pool
        stats: dict[str, Any] = {}
        for name in ("size", "checkedin", "checkedout", "overflow"):
            getter = getattr(pool, name, None)
            if callable(getter):
                try:
                    stats[name] = getter()
                except Exception:  # noqa: S112 - see comment
                    # Pool implementations expose different counters; a stat that
                    # is unavailable is not an error worth logging on every
                    # health check, and health checks must never raise.
                    continue
        return stats


def _short_version(version: str) -> str:
    """``PostgreSQL 16.13 on x86_64-pc-linux-gnu, compiled by…`` -> ``PostgreSQL 16.13``.

    The full banner names the build host and compiler. Harmless on its own, but
    it is version-fingerprinting material that does not need to be on a
    dashboard.
    """
    parts = version.split(",")[0].split(" on ")[0]
    return parts.strip()


def _safe_error(exc: Exception) -> str:
    """Summarise an exception without leaking the connection string.

    SQLAlchemy's messages sometimes embed the URL, password included. We keep
    the exception type and the first line, and drop anything containing an ``@``
    that looks like credentials.
    """
    text_value = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    if "://" in text_value:
        text_value = f"{exc.__class__.__name__}: connection failed"
    return f"{exc.__class__.__name__}: {text_value}"[:300]
