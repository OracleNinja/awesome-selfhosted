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

import time
from dataclasses import dataclass
from typing import Any

from nexus.core.config import Settings
from nexus.core.logging import get_logger
from nexus.db.session import Database

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
        """
        self._started_at = time.monotonic()
        database_ok = await self.database.connect()
        self._ready = database_ok
        logger.info(
            "context_started",
            extra={"ready": self._ready, "database_ok": database_ok},
        )

    async def shutdown(self) -> None:
        """Release resources in reverse order of acquisition.

        Reverse order because a subsystem may depend on one started before it;
        shutting down the database first would break anything still draining.
        """
        self._ready = False
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

        return components

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
