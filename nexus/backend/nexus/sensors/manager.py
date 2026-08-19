"""Supervises sensors: starts them, restarts them, and reports on them.

A sensor is a long-lived task that can fail in ways nothing else in the system
can: an interface disappears when a cable is unplugged, a socket dies when a
VPN drops, a device floods the syslog port. The manager's job is to make those
failures survivable and *visible*.

Supervision policy
------------------
* Each sensor runs in its own task. One sensor crashing cannot take down
  another, and cannot take down the API.
* A crashed sensor restarts with exponential backoff. Restarting instantly in a
  loop turns a broken configuration into a busy loop that hides the error;
  waiting longer each time leaves the message readable and the CPU idle.
* After a burst of restarts the sensor is parked in ``FAILED`` with the reason
  on its row. A sensor that cannot run must *say so* — silently retrying
  forever is how an operator ends up believing they are monitored when they
  are not.
* Availability is re-checked before each restart, so an unplugged interface
  reports "interface not found" rather than a generic crash.

Back-pressure
-------------
Observations are buffered and flushed in batches, but the buffer is bounded. If
ingestion cannot keep up, the sensor's generator is simply not pulled from, and
the pressure propagates back to the driver — which is why sensors are pull-based
generators. The one exception is the syslog receiver, which cannot slow down a
UDP sender and therefore drops with a counter and emits an event saying so.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from nexus.core.context import ComponentHealth
from nexus.core.logging import component_var, get_logger
from nexus.db.base import utcnow
from nexus.db.models.sensor import Sensor as SensorRow
from nexus.sensors import resolve_driver
from nexus.sensors.base import Availability, RawObservation, Sensor
from nexus.services.ingest import IngestionService

logger = get_logger(__name__)

# Flush when either threshold is hit: enough events to be worth a round trip,
# or enough time that waiting longer would make the dashboard feel stale.
FLUSH_BATCH_SIZE = 200
FLUSH_INTERVAL_SECONDS = 2.0

# Bounded buffer. If ingestion stalls, the sensor stops being pulled rather
# than memory growing without limit.
MAX_BUFFER = 5_000

# How often the manager re-reads the sensors table on its own. This is the
# convergence path: whatever else happens, the running set is correct within
# this interval, including after a change made by another process.
RECONCILE_INTERVAL_SECONDS = 30.0

# After a change is signalled, wait this long before re-reading. Two reasons:
#
# 1. The signal arrives from inside a request handler, whose transaction has
#    not committed yet — the manager reads in its own session and would not see
#    the row. (A FastAPI background task does not help: it runs *before*
#    yield-dependency teardown, which is where the commit happens.)
# 2. It coalesces a burst of changes into one reload.
RELOAD_DEBOUNCE_SECONDS = 0.5

RESTART_BASE_DELAY = 2.0
RESTART_MAX_DELAY = 300.0
# After this many consecutive failures the sensor is parked rather than
# restarted, so a permanent problem is visible instead of being retried forever.
MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class SensorRuntime:
    """Live state for one running sensor."""

    row_id: Any
    name: str
    driver: str
    task: asyncio.Task | None = None
    status: str = "STOPPED"
    detail: str | None = None
    consecutive_failures: int = 0
    events_ingested: int = 0
    events_duplicate: int = 0
    events_rejected: int = 0
    last_event_at: float | None = None
    started_at: float | None = None
    restarts: int = 0
    recent_errors: list[str] = field(default_factory=list)


class SensorManager:
    """Owns every running sensor in this process."""

    def __init__(self, context) -> None:
        self._context = context
        self._settings = context.settings
        self._runtimes: dict[Any, SensorRuntime] = {}
        self._stopping = False
        self._reconciler: asyncio.Task | None = None
        self._reload_requested = asyncio.Event()

    # ------------------------------------------------------------ lifecycle --

    async def start(self) -> None:
        """Start every enabled sensor.

        A failure to start one sensor is recorded on its row and does not stop
        the others, or the platform.
        """
        component_var.set("sensors")
        self._stopping = False
        rows = await self._load_enabled()
        for row in rows:
            await self._spawn(row)
        self._reconciler = asyncio.create_task(self._reconcile_loop())
        logger.info("sensor_manager_started", extra={"sensors": len(self._runtimes)})

    async def stop(self) -> None:
        """Cancel every sensor task and wait for them to release resources."""
        self._stopping = True
        if self._reconciler is not None:
            self._reconciler.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reconciler
            self._reconciler = None
        tasks = [runtime.task for runtime in self._runtimes.values() if runtime.task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._runtimes.clear()
        logger.info("sensor_manager_stopped")

    async def reload(self) -> None:
        """Apply configuration changes without restarting the process.

        Sensors that disappeared or were disabled are stopped; new ones are
        started. An operator changing a sensor's configuration should not have
        to restart NEXUS — that would interrupt every *other* sensor too.
        """
        rows = await self._load_enabled()
        wanted = {row.id: row for row in rows}

        for row_id in list(self._runtimes):
            if row_id not in wanted:
                await self._despawn(row_id)

        for row_id, row in wanted.items():
            if row_id not in self._runtimes:
                await self._spawn(row)

    def request_reload(self) -> None:
        """Ask the manager to re-read its configuration soon.

        Synchronous and non-blocking, so an API handler can call it directly.
        The manager does the actual work in its own task after a short debounce,
        which is what keeps it from reading the caller's uncommitted
        transaction.
        """
        self._reload_requested.set()

    async def _reconcile_loop(self) -> None:
        """Bring running sensors in line with the database.

        Wakes either on a signal (a configuration change just happened) or on a
        timer (convergence, including for changes made by another process). The
        timer is what makes this self-healing: a lost signal costs at most one
        interval, never a sensor that never starts.
        """
        while not self._stopping:
            try:
                await asyncio.wait_for(
                    self._reload_requested.wait(), timeout=RECONCILE_INTERVAL_SECONDS
                )
                self._reload_requested.clear()
                # Let the requesting transaction commit, and coalesce a burst.
                await asyncio.sleep(RELOAD_DEBOUNCE_SECONDS)
            except TimeoutError:
                pass  # periodic reconcile

            try:
                await self.reload()
            except Exception:  # pragma: no cover - defensive
                logger.warning("sensor_reconcile_failed")

    async def _load_enabled(self) -> list[SensorRow]:
        async with self._context.database.session() as session:
            result = await session.execute(
                select(SensorRow).where(SensorRow.enabled.is_(True)).order_by(SensorRow.name)
            )
            rows = list(result.scalars().all())
            for row in rows:
                session.expunge(row)
            return rows

    async def _spawn(self, row: SensorRow) -> None:
        runtime = SensorRuntime(row_id=row.id, name=row.name, driver=row.driver)
        self._runtimes[row.id] = runtime
        runtime.task = asyncio.create_task(self._supervise(row, runtime), name=f"sensor:{row.name}")

    async def _despawn(self, row_id: Any) -> None:
        runtime = self._runtimes.pop(row_id, None)
        if runtime and runtime.task:
            runtime.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await runtime.task

    # ----------------------------------------------------------- supervision --

    async def _supervise(self, row: SensorRow, runtime: SensorRuntime) -> None:
        """Run one sensor, restarting it with backoff when it fails."""
        delay = RESTART_BASE_DELAY
        while not self._stopping:
            try:
                driver_class = resolve_driver(row.driver)
            except Exception as exc:
                await self._mark(runtime, "FAILED", f"Unknown driver: {exc}")
                return

            report = driver_class.check_availability(row.config or {}, self._settings)
            if not report.is_available:
                # NOT_AVAILABLE and NOT_CONFIGURED are different states with
                # different remedies, and both are shown verbatim to the
                # operator rather than collapsed into "error".
                status = (
                    "NOT_AVAILABLE"
                    if report.status is Availability.NOT_AVAILABLE
                    else "NOT_CONFIGURED"
                )
                detail = report.detail + (f" {report.remedy}" if report.remedy else "")
                await self._mark(runtime, status, detail)
                return

            await self._mark(runtime, "STARTING", report.detail)
            sensor = driver_class(row.name, row.config or {}, self._settings)

            try:
                runtime.started_at = asyncio.get_running_loop().time()
                await self._mark(runtime, "RUNNING", report.detail)
                await self._pump(sensor, row, runtime)
                # A generator that returns cleanly means the sensor finished
                # its work — a lab scenario playing out, for instance. That is
                # COMPLETED, not STOPPED and certainly not a failure.
                await self._mark(runtime, "COMPLETED", "Sensor completed its run.")
                return
            except asyncio.CancelledError:
                await sensor.stop()
                await self._mark(runtime, "STOPPED", "Stopped.")
                raise
            except Exception as exc:
                runtime.consecutive_failures += 1
                runtime.restarts += 1
                message = f"{exc.__class__.__name__}: {exc}"[:400]
                runtime.recent_errors = [*runtime.recent_errors, message][-5:]
                logger.exception(
                    "sensor_failed",
                    extra={
                        "sensor": row.name,
                        "driver": row.driver,
                        "consecutive_failures": runtime.consecutive_failures,
                    },
                )
                with contextlib.suppress(Exception):
                    await sensor.stop()

                if runtime.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    await self._mark(
                        runtime,
                        "FAILED",
                        f"Stopped after {runtime.consecutive_failures} consecutive "
                        f"failures. Last error: {message}",
                        error=message,
                    )
                    return

                await self._mark(
                    runtime, "DEGRADED", f"Restarting after error: {message}", error=message
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RESTART_MAX_DELAY)

    async def _pump(self, sensor: Sensor, row: SensorRow, runtime: SensorRuntime) -> None:
        """Consume the sensor's observations and ingest them in batches."""
        buffer: list[RawObservation] = []
        loop = asyncio.get_running_loop()
        last_flush = loop.time()

        async def flush() -> None:
            nonlocal last_flush, buffer
            if not buffer:
                last_flush = loop.time()
                return
            batch, buffer = buffer, []
            await self._ingest(batch, row, runtime, sensor.produces_simulated_data)
            last_flush = loop.time()

        try:
            async for observation in sensor.observations():
                buffer.append(observation)
                runtime.last_event_at = loop.time()

                if len(buffer) >= FLUSH_BATCH_SIZE or (
                    loop.time() - last_flush >= FLUSH_INTERVAL_SECONDS
                ):
                    await flush()

                if len(buffer) >= MAX_BUFFER:  # pragma: no cover - defensive
                    logger.warning(
                        "sensor_buffer_full", extra={"sensor": row.name, "buffered": len(buffer)}
                    )
                    await flush()
        finally:
            # Flush whatever is buffered on the way out, so a clean shutdown
            # does not discard observations already made. A failure here is
            # logged rather than suppressed: silently losing observations is
            # exactly the class of bug this project must not have.
            try:
                await flush()
            except Exception:
                logger.exception(
                    "sensor_final_flush_failed",
                    extra={"sensor": row.name, "buffered": len(buffer)},
                )

    async def _ingest(
        self,
        observations: list[RawObservation],
        row: SensorRow,
        runtime: SensorRuntime,
        is_simulation: bool,
    ) -> None:
        try:
            async with self._context.database.session() as session:
                sensor_row = await session.get(SensorRow, row.id)
                if sensor_row is None:  # deleted while running
                    return
                result = await IngestionService(session).ingest(
                    observations, sensor_row, driver_is_simulation=is_simulation
                )
                sensor_row.last_heartbeat_at = utcnow()

            runtime.events_ingested += result.stored
            runtime.events_duplicate += result.duplicates
            runtime.events_rejected += result.rejected
            runtime.consecutive_failures = 0
        except Exception as exc:
            # A database outage must not kill the sensor. The observations in
            # this batch are lost — they were never persisted, and pretending
            # otherwise would be worse — but the sensor keeps running and
            # recovers when the database does.
            logger.error(
                "ingestion_failed",
                extra={
                    "sensor": row.name,
                    "observations": len(observations),
                    "error": exc.__class__.__name__,
                },
            )
            runtime.events_rejected += len(observations)

    # --------------------------------------------------------------- status --

    async def _mark(
        self,
        runtime: SensorRuntime,
        status: str,
        detail: str | None = None,
        *,
        error: str | None = None,
    ) -> None:
        """Record status on the runtime and on the row.

        Written to the database as well as held in memory so the API can report
        a sensor's state without talking to the process that runs it — which
        matters as soon as workers and the API are separate processes.
        """
        runtime.status = status
        runtime.detail = detail
        try:
            async with self._context.database.session() as session:
                row = await session.get(SensorRow, runtime.row_id)
                if row is None:
                    return
                row.status = status
                row.status_detail = (detail or "")[:500] or None
                row.last_heartbeat_at = utcnow()
                if error:
                    row.last_error = error[:500]
                    row.last_error_at = utcnow()
        except Exception:  # pragma: no cover - status is best-effort
            logger.debug("sensor_status_write_failed", extra={"sensor": runtime.name})

    def health(self) -> list[ComponentHealth]:
        """One component per sensor, for the health endpoint."""
        components: list[ComponentHealth] = []
        for runtime in self._runtimes.values():
            if runtime.status in ("RUNNING", "COMPLETED", "STOPPED"):
                # STOPPED and COMPLETED are deliberate states, not faults: an
                # operator disabled it, or a finite source finished. Colouring
                # either of those red is how a dashboard trains people to
                # ignore red.
                status = "ok"
            elif runtime.status in ("NOT_AVAILABLE", "NOT_CONFIGURED"):
                # Not a failure: the operator either has not set it up or
                # cannot on this host. A dashboard that shows red for that
                # trains people to ignore red.
                status = "not_configured"
            elif runtime.status in ("STARTING", "DEGRADED"):
                status = "degraded"
            else:
                status = "unavailable"

            components.append(
                ComponentHealth(
                    name=f"sensor:{runtime.name}",
                    status=status,
                    detail=runtime.detail,
                    data={
                        "driver": runtime.driver,
                        "sensor_status": runtime.status,
                        "events_ingested": runtime.events_ingested,
                        "events_duplicate": runtime.events_duplicate,
                        "events_rejected": runtime.events_rejected,
                        "restarts": runtime.restarts,
                    },
                )
            )
        return components

    def describe(self) -> list[dict[str, Any]]:
        """Runtime detail for the sensors screen."""
        return [
            {
                "name": runtime.name,
                "driver": runtime.driver,
                "status": runtime.status,
                "detail": runtime.detail,
                "events_ingested": runtime.events_ingested,
                "events_duplicate": runtime.events_duplicate,
                "events_rejected": runtime.events_rejected,
                "restarts": runtime.restarts,
                "recent_errors": list(runtime.recent_errors),
            }
            for runtime in self._runtimes.values()
        ]
