"""The composition root.

Every non-trivial application needs one place where concrete implementations
are chosen and wired together. Scatter that decision — a module-level engine
here, a global client there — and you get an application that can only be
constructed one way: the production way. Tests then resort to monkeypatching
globals, and a subsystem cannot be exercised in isolation.

:class:`AppContext` is that one place. It owns the long-lived objects (database
pool, and in later milestones the job queue, event bus, and sensor manager),
starts and stops them in a defined order, and reports their health. The HTTP
layer receives it through ``app.state`` and hands it to routes via FastAPI
dependencies; workers construct their own with the same code path.

This is dependency injection without a framework: constructor parameters and
one owner. A DI container would add indirection without adding capability at
this size.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus.core.config import Settings
from nexus.core.logging import get_logger
from nexus.core.passwords import PasswordService
from nexus.core.ratelimit import SlidingWindowRateLimiter
from nexus.db.session import Database

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from nexus.sensors.manager import SensorManager
    from nexus.workers.worker import Worker

logger = get_logger(__name__)


@dataclass(frozen=True)
class ComponentHealth:
    """Health of one subsystem.

    ``status`` is one of:

    ``ok``              working normally
    ``degraded``        working with reduced capability or elevated errors
    ``unavailable``     configured, but currently failing
    ``not_configured``  the operator has not set this up

    The last value is the honesty requirement made structural: an unconfigured
    threat-intelligence provider reports ``not_configured``, and the UI says
    NOT CONFIGURED rather than showing a reassuring green tick over a feature
    that is not running.
    """

    name: str
    status: str
    detail: str | None = None
    data: dict[str, Any] | None = None

    @property
    def is_healthy(self) -> bool:
        return self.status == "ok"


class AppContext:
    """Owns process-wide resources and their lifecycle."""

    def __init__(self, settings: Settings, *, database: Database | None = None) -> None:
        self.settings = settings
        # Injectable so tests can supply a database pointed at a scratch schema
        # without touching the environment or a global.
        self.database = database or Database(settings)

        # Built once per process, not per request: constructing a PasswordService
        # computes a dummy Argon2 hash (tens of milliseconds by design), and
        # paying that on every login would double the cost of the endpoint we
        # most want to keep responsive under attack.
        self.passwords = PasswordService(settings)

        # Rate limiters are per process and in memory by design; see
        # nexus.core.ratelimit for why that is the right trade here.
        self.login_rate_limiter = SlidingWindowRateLimiter(
            limit=settings.login_rate_limit_per_minute, window_seconds=60.0
        )
        self.api_rate_limiter = SlidingWindowRateLimiter(
            limit=settings.api_rate_limit_per_minute, window_seconds=60.0
        )

        # Started during `startup` only when configuration enables them, so a
        # deployment can split the API and the workers into separate processes
        # without a different codebase.
        self.sensors: SensorManager | None = None
        self.worker: Worker | None = None
        self._worker_task: asyncio.Task | None = None

        self._started_at: float | None = None
        self._ready = False

    # ------------------------------------------------------------ lifecycle --

    async def startup(self) -> None:
        """Bring subsystems up in dependency order.

        Startup is deliberately tolerant: a failing database logs an error and
        leaves the process *not ready* rather than aborting. The distinction
        matters to an orchestrator — a container that exits is restarted in a
        loop, whereas a container that reports unready is left alone to recover
        and can still serve its health endpoint to explain itself.

        Sensors and workers start only after the database is confirmed
        reachable. Starting a sensor with nowhere to store its observations
        would discard real data and report success.
        """
        self._started_at = time.monotonic()
        database_ok = await self.database.connect()
        self._ready = database_ok

        if database_ok:
            await self._start_background()

        logger.info(
            "context_started",
            extra={
                "ready": self._ready,
                "database_ok": database_ok,
                "sensors": self.sensors is not None,
                "worker": self.worker is not None,
            },
        )

    async def _start_background(self) -> None:
        """Start sensors and the worker, each failing independently.

        Imported here rather than at module scope: `nexus.sensors.manager`
        imports the ingestion service, which imports models, which import this
        module. A local import keeps the dependency one-directional at import
        time while still letting the context own the objects.
        """
        if self.settings.run_sensors:
            from nexus.sensors.manager import SensorManager

            try:
                self.sensors = SensorManager(self)
                await self.sensors.start()
            except Exception:
                # A broken sensor configuration must not prevent the API from
                # serving — an operator needs the UI in order to fix it.
                logger.exception("sensor_manager_start_failed")
                self.sensors = None

        if self.settings.run_workers:
            from nexus.workers.worker import Worker

            try:
                # Importing the handlers package is what registers job kinds.
                import nexus.workers.handlers  # noqa: F401

                self.worker = Worker(self)
                self._worker_task = asyncio.create_task(self.worker.run(), name="worker")
            except Exception:
                logger.exception("worker_start_failed")
                self.worker = None

    async def shutdown(self) -> None:
        """Release resources in reverse order of acquisition.

        Reverse order because a subsystem may depend on one started before it;
        shutting down the database first would break anything still draining.
        """
        self._ready = False

        if self.worker is not None:
            self.worker.stop()
        if self._worker_task is not None:
            # Bounded wait: in-flight jobs get a chance to finish, but a stuck
            # job must not prevent the process from exiting — its lock will
            # expire and another worker will retry it.
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._worker_task, timeout=35)
            self._worker_task = None

        if self.sensors is not None:
            with contextlib.suppress(Exception):
                await self.sensors.stop()
            self.sensors = None

        await self.database.dispose()
        logger.info("context_stopped")

    # --------------------------------------------------------------- status --

    @property
    def uptime_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return round(time.monotonic() - self._started_at, 3)

    @property
    def is_ready(self) -> bool:
        return self._ready

    async def check_components(self) -> list[ComponentHealth]:
        """Probe every subsystem. Never raises.

        Later milestones append their components here (workers, queue, sensors,
        threat intel), which is why the return type is a list rather than a
        fixed structure: the health endpoint renders whatever is present, so a
        new subsystem becomes observable by being registered, not by editing
        the endpoint.
        """
        components: list[ComponentHealth] = []

        db_health = await self.database.check_health()
        if db_health.ok:
            components.append(
                ComponentHealth(
                    name="database",
                    status="ok",
                    data={
                        "latency_ms": db_health.latency_ms,
                        "server_version": db_health.server_version,
                        "pool": db_health.pool,
                    },
                )
            )
            # A previously-degraded process becomes ready again the moment the
            # database recovers, with no restart required.
            self._ready = True
        else:
            components.append(
                ComponentHealth(
                    name="database",
                    status="unavailable",
                    detail=db_health.error,
                    data={"pool": db_health.pool},
                )
            )
            self._ready = False
            # Nothing below can be probed without a database, and probing them
            # would produce a cascade of misleading failures.
            return components

        components.extend(await self._queue_health())

        if self.sensors is not None:
            components.extend(self.sensors.health())
        elif self.settings.run_sensors:
            components.append(
                ComponentHealth(
                    name="sensors",
                    status="unavailable",
                    detail="The sensor manager failed to start; see the logs.",
                )
            )

        components.append(self._worker_health())
        return components

    async def _queue_health(self) -> list[ComponentHealth]:
        """Queue depth, and whether work is actually moving.

        The number that matters is not the backlog size but the age of the
        oldest waiting job: a big queue draining fast is a busy system, while a
        small queue with an hour-old job is a stopped one.
        """
        from nexus.services.jobs import JobQueue

        try:
            async with self.database.session() as session:
                stats = await JobQueue(session).stats()
        except Exception as exc:
            return [
                ComponentHealth(
                    name="job_queue",
                    status="unavailable",
                    detail=f"Queue statistics unavailable: {exc.__class__.__name__}",
                )
            ]

        detail = None
        status = "ok"
        if stats.dead:
            status = "degraded"
            detail = f"{stats.dead} job(s) exhausted their retries and need attention."
        elif not stats.is_healthy:
            status = "degraded"
            detail = (
                f"Oldest pending job is {int(stats.oldest_pending_age_seconds or 0)}s old; "
                "workers may be stopped or overloaded."
            )

        return [
            ComponentHealth(
                name="job_queue",
                status=status,
                detail=detail,
                data={
                    "pending": stats.pending,
                    "running": stats.running,
                    "dead": stats.dead,
                    "oldest_pending_age_seconds": stats.oldest_pending_age_seconds,
                },
            )
        ]

    def _worker_health(self) -> ComponentHealth:
        if self.worker is None:
            if not self.settings.run_workers:
                # A deliberate choice, not a fault: this process serves the API
                # and separate worker processes do the work.
                return ComponentHealth(
                    name="worker",
                    status="not_configured",
                    detail="Workers are disabled in this process (NEXUS_RUN_WORKERS=false).",
                )
            return ComponentHealth(
                name="worker", status="unavailable", detail="The worker failed to start."
            )

        stats = self.worker.stats
        return ComponentHealth(
            name="worker",
            status="ok",
            data={
                "worker_id": self.worker.worker_id,
                "in_flight": stats.in_flight,
                "succeeded": stats.succeeded,
                "failed": stats.failed,
                "timed_out": stats.timed_out,
                "reclaimed": stats.reclaimed,
            },
        )

    async def overall_status(self) -> tuple[str, list[ComponentHealth]]:
        """Aggregate component health into one word.

        ``unavailable`` beats ``degraded`` beats ``ok``. ``not_configured`` is
        *not* a failure — it is a deliberate operator choice, and a dashboard
        that shows red for "you have not enabled the optional integration"
        trains people to ignore red.
        """
        components = await self.check_components()
        if any(component.status == "unavailable" for component in components):
            return "unavailable", components
        if any(component.status == "degraded" for component in components):
            return "degraded", components
        return "ok", components
