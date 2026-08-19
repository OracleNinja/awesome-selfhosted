"""The sensor interface.

A sensor is anything that observes the network and reports what it saw. The
interface is deliberately narrow — start, stop, yield observations, report
health — because the implementations behind it are wildly different: one reads
a kernel table, one opens a raw socket, one listens on a UDP port. Everything
downstream (normalisation, storage, detection, the UI) works against this one
shape and never learns which driver produced a given event.

Honesty is enforced structurally
--------------------------------
Two mechanisms, not conventions:

* ``check_availability()`` is a **class method that runs before a sensor is
  started**, and reports ``NOT_AVAILABLE`` with a human-readable reason when the
  platform cannot support the driver — no permission, wrong operating system,
  no such interface. The UI shows that reason. A sensor that cannot run never
  silently reports "no events found", which would read as "your network is
  quiet" when it means "nothing is watching".
* ``produces_simulated_data`` is a class attribute, and every observation from
  such a driver is stamped ``is_simulation`` at ingestion. A lab driver cannot
  produce data that looks real, because the flag is set from the driver's
  identity rather than by remembering to pass it.

Push versus pull
----------------
Sensors are async generators (pull) rather than callback-based (push). The
consumer therefore controls the rate: if ingestion is slow, back-pressure
propagates naturally to the sensor instead of an unbounded queue growing in
memory until the process dies. For sources that are genuinely push-based — a
UDP socket receiving syslog — the driver owns a bounded internal queue and
documents what it does when that queue is full. Dropping with a counter is
honest; growing without bound is not.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from nexus.core.config import Settings
from nexus.core.logging import get_logger
from nexus.db.base import utcnow

logger = get_logger(__name__)

# A stable namespace for deriving deterministic event ids. Fixed forever: the
# whole point is that the same observation yields the same UUID across
# restarts, so a re-delivered event is recognised as a duplicate.
EVENT_UID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

MAX_RAW_LENGTH = 8192
MAX_SUMMARY_LENGTH = 500


class Availability(str, Enum):
    """Whether a driver can run here, and if not, which kind of "not"."""

    AVAILABLE = "AVAILABLE"
    # The platform cannot support it: no raw-socket capability, wrong OS,
    # missing kernel interface. Fixable only by changing the deployment.
    NOT_AVAILABLE = "NOT_AVAILABLE"
    # It could run, but the operator has not supplied what it needs.
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class AvailabilityReport:
    status: Availability
    detail: str
    # What the operator would have to do. Shown verbatim in the UI, because
    # "NOT_AVAILABLE" with no remedy is a dead end.
    remedy: str | None = None

    @property
    def is_available(self) -> bool:
        return self.status is Availability.AVAILABLE

    @classmethod
    def available(cls, detail: str = "Ready.") -> AvailabilityReport:
        return cls(status=Availability.AVAILABLE, detail=detail)

    @classmethod
    def unavailable(cls, detail: str, remedy: str | None = None) -> AvailabilityReport:
        return cls(status=Availability.NOT_AVAILABLE, detail=detail, remedy=remedy)

    @classmethod
    def unconfigured(cls, detail: str, remedy: str | None = None) -> AvailabilityReport:
        return cls(status=Availability.NOT_CONFIGURED, detail=detail, remedy=remedy)


@dataclass
class RawObservation:
    """One thing a sensor saw, before normalisation.

    Sensors fill in what they know and leave the rest ``None``. Guessing is
    forbidden: a driver that cannot see a MAC address leaves ``mac_address``
    empty rather than inventing one, and a driver that cannot tell whether a
    flow was inbound or outbound says nothing rather than picking.
    """

    kind: str  # "device.discovered", "flow.observed", "syslog.message"
    category: str  # one of EVENT_CATEGORIES
    summary: str

    occurred_at: datetime = field(default_factory=utcnow)
    severity: str = "INFO"
    confidence: int = 100

    source_ip: str | None = None
    destination_ip: str | None = None
    source_port: int | None = None
    destination_port: int | None = None
    protocol: str | None = None
    mac_address: str | None = None
    hostname: str | None = None

    attributes: dict[str, Any] = field(default_factory=dict)
    raw: str | None = None

    # Inputs to the deterministic event id. Two observations of the *same
    # real-world fact* must produce the same seed, and two different facts must
    # not. For a periodic device sighting that is (mac, minute-bucket); for a
    # syslog line it is the line's own content and timestamp.
    uid_seed: str | None = None

    def event_uid(self, sensor_name: str) -> uuid.UUID:
        """Derive this observation's idempotency key.

        UUID5 (name-based, SHA-1) rather than UUID4: the id is a pure function
        of the observation, so a sensor restarting and re-reading the same
        state produces the same id, and the unique index discards the duplicate.
        Without this, every restart would re-import history and inflate every
        count and threshold downstream.

        The sensor name is part of the seed so two sensors observing the same
        fact produce two events — they are two independent pieces of evidence,
        and collapsing them would hide that one of them stopped working.
        """
        seed = self.uid_seed or f"{self.kind}|{self.occurred_at.isoformat()}|{self.summary}"
        return uuid.uuid5(EVENT_UID_NAMESPACE, f"{sensor_name}|{seed}")

    def truncated(self) -> RawObservation:
        """Enforce field limits before storage.

        Every string here is attacker-influenced: a hostname from DHCP, a
        syslog line from any device on the network. Truncation happens once, at
        the boundary, so no downstream code has to remember that a 10 MB
        "hostname" is possible.
        """
        self.summary = self.summary[:MAX_SUMMARY_LENGTH]
        if self.raw is not None:
            self.raw = self.raw[:MAX_RAW_LENGTH]
        if self.hostname is not None:
            self.hostname = self.hostname[:255]
        return self


class Sensor(ABC):
    """Base class for sensor drivers."""

    # Stable identifier stored in `sensors.driver` and in every event.
    driver: ClassVar[str]
    # One line for the "add a sensor" screen.
    description: ClassVar[str] = ""
    # True only for laboratory drivers. Everything they emit is flagged
    # is_simulation at ingestion, from this attribute rather than from a
    # parameter someone could forget to pass.
    produces_simulated_data: ClassVar[bool] = False
    # Whether the driver needs elevated capability (raw sockets). Shown in the
    # UI so an operator understands why a sensor is unavailable.
    requires_privileges: ClassVar[bool] = False

    def __init__(self, name: str, config: dict[str, Any], settings: Settings) -> None:
        self.name = name
        self.config = config
        self.settings = settings
        self._running = False

    # -------------------------------------------------------- availability --

    @classmethod
    @abstractmethod
    def check_availability(cls, config: dict[str, Any], settings: Settings) -> AvailabilityReport:
        """Can this driver run here, with this configuration?

        Called before starting, and again by the health check. Must be cheap
        and must never raise: it is the thing an operator consults when
        something is not working, so it has to work when things are not.
        """

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise driver configuration.

        Raises :class:`~nexus.core.errors.ValidationFailed` on bad input. The
        default accepts anything; drivers with required settings override it.
        """
        return config

    # ------------------------------------------------------------ lifecycle --

    @abstractmethod
    async def observations(self) -> AsyncIterator[RawObservation]:
        """Yield observations until cancelled.

        Implementations must be cancellation-safe: when the task running this
        generator is cancelled, sockets and file handles have to be released.
        A `try/finally` around the loop is the usual shape.
        """
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for type checkers

    async def stop(self) -> None:
        """Release resources. Called after the generator task is cancelled."""
        self._running = False

    def describe(self) -> dict[str, Any]:
        """Non-sensitive summary for the sensors screen."""
        return {
            "name": self.name,
            "driver": self.driver,
            "simulation": self.produces_simulated_data,
            "requires_privileges": self.requires_privileges,
        }
