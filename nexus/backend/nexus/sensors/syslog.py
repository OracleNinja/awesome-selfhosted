"""Syslog receiver.

Most network equipment cannot run an agent, but nearly all of it can forward
syslog: routers, switches, access points, firewalls, NAS boxes, printers. That
makes a syslog listener the highest-value sensor per line of code — it is how
NEXUS learns about DHCP leases, failed SSH logins, firewall drops, and link
state on devices it cannot otherwise inspect.

Two wire formats, both real
---------------------------
* **RFC 3164** (the old BSD format) — ``<134>Oct 11 22:14:15 host tag: message``.
  The timestamp has no year and no timezone, which is exactly as bad as it
  sounds; the receive time is recorded alongside so an event's real position in
  a timeline is never lost.
* **RFC 5424** (the structured one) — ``<134>1 2026-08-18T20:14:15.003Z host app
  pid msgid [sd] message``. Full ISO timestamp, structured data.

Everything is untrusted
-----------------------
UDP syslog has no authentication and no delivery guarantee. Any host that can
reach the port can send anything, including forged hostnames and messages
crafted to look like something else. So:

* the source address is recorded from the *packet*, not from the message — the
  one field the sender cannot lie about;
* fields are length-capped and control characters stripped before storage,
  because these strings end up in a UI and in log files;
* severity from the message is recorded as the *sender's claim*, never trusted
  as NEXUS's own assessment;
* a bounded queue drops under flood, with a counter, rather than growing until
  the process dies. Anyone on the network can send unlimited UDP; the receiver
  must survive that.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar

from nexus.core.config import Settings
from nexus.core.errors import ValidationFailed
from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.sensors.base import AvailabilityReport, RawObservation, Sensor

logger = get_logger(__name__)

# Above 1024, so no privilege is needed. Senders are configured to match.
DEFAULT_PORT = 5514
DEFAULT_BIND = "0.0.0.0"  # noqa: S104 - a listener must bind somewhere reachable

# Bounded, so a flood costs bounded memory. Sized for a burst of a few seconds
# at a rate far above anything a home network produces.
QUEUE_SIZE = 10_000
MAX_MESSAGE_BYTES = 8192

# <PRI> = facility * 8 + severity.
SYSLOG_SEVERITIES = {
    0: ("CRITICAL", "emergency"),
    1: ("CRITICAL", "alert"),
    2: ("CRITICAL", "critical"),
    3: ("HIGH", "error"),
    4: ("MEDIUM", "warning"),
    5: ("INFO", "notice"),
    6: ("INFO", "informational"),
    7: ("INFO", "debug"),
}

SYSLOG_FACILITIES = {
    0: "kernel",
    1: "user",
    2: "mail",
    3: "daemon",
    4: "auth",
    5: "syslog",
    6: "lpr",
    7: "news",
    8: "uucp",
    9: "cron",
    10: "authpriv",
    11: "ftp",
    16: "local0",
    17: "local1",
    18: "local2",
    19: "local3",
    20: "local4",
    21: "local5",
    22: "local6",
    23: "local7",
}

_RFC5424_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>1 (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) "
    r"(?P<pid>\S+) (?P<msgid>\S+) (?P<rest>.*)$",
    re.DOTALL,
)
_RFC3164_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ts>[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2}) "
    r"(?P<host>\S+) (?P<rest>.*)$",
    re.DOTALL,
)
_TAG_RE = re.compile(
    r"^(?P<tag>[\w\-./]{1,32})(?:\[(?P<pid>\d+)\])?:\s*(?P<message>.*)$", re.DOTALL
)

# Strip C0 control characters except tab. A raw \r or an ANSI escape in a log
# line can rewrite a terminal's output — log injection against whoever runs
# `tail` on the file.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class SyslogSensor(Sensor):
    driver: ClassVar[str] = "syslog"
    description: ClassVar[str] = (
        "Receives syslog over UDP from routers, firewalls and other network "
        "equipment. Parses RFC 3164 and RFC 5424."
    )
    produces_simulated_data: ClassVar[bool] = False
    requires_privileges: ClassVar[bool] = False

    def __init__(self, name: str, config: dict[str, Any], settings: Settings) -> None:
        super().__init__(name, config, settings)
        self._bind = config.get("bind_address", DEFAULT_BIND)
        self._port = int(config.get("port", DEFAULT_PORT))
        # Optional allow-list of senders. Not authentication — UDP source
        # addresses are forgeable — but it does stop accidental ingestion from
        # a misconfigured neighbour.
        self._allowed = set(config.get("allowed_sources") or [])
        self._queue: asyncio.Queue[tuple[bytes, str]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._transport: asyncio.DatagramTransport | None = None
        self._dropped = 0

    # -------------------------------------------------------- availability --

    @classmethod
    def check_availability(cls, config: dict[str, Any], settings: Settings) -> AvailabilityReport:
        port = int(config.get("port", DEFAULT_PORT))
        if port < 1024:
            return AvailabilityReport.unconfigured(
                f"Port {port} is privileged.",
                remedy=(
                    "Use a port above 1024 (5514 by default) and forward 514 to "
                    "it at the firewall, rather than running NEXUS as root."
                ),
            )
        return AvailabilityReport.available(
            f"Will listen for syslog on UDP {config.get('bind_address', DEFAULT_BIND)}:{port}. "
            "Configure your devices to forward there."
        )

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        port = int(config.get("port", DEFAULT_PORT))
        if not 1024 <= port <= 65535:
            raise ValidationFailed("port must be between 1024 and 65535.")
        bind = config.get("bind_address", DEFAULT_BIND)
        if not isinstance(bind, str):
            raise ValidationFailed("bind_address must be a string.")
        sources = config.get("allowed_sources") or []
        if not isinstance(sources, list) or any(not isinstance(item, str) for item in sources):
            raise ValidationFailed("allowed_sources must be a list of IP addresses.")
        return {"port": port, "bind_address": bind, "allowed_sources": sources}

    # ------------------------------------------------------------ operation --

    async def observations(self) -> AsyncIterator[RawObservation]:
        loop = asyncio.get_running_loop()
        self._transport, _protocol = await loop.create_datagram_endpoint(
            lambda: _SyslogProtocol(self),
            local_addr=(self._bind, self._port),
        )
        self._running = True
        logger.info(
            "syslog_listening",
            extra={"sensor": self.name, "bind": self._bind, "port": self._port},
        )

        try:
            while self._running:
                try:
                    payload, source = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except TimeoutError:
                    # Surfacing drops needs to happen even when no messages are
                    # arriving, or a flood that stops looks like it never
                    # happened.
                    if self._dropped:
                        yield self._drop_report()
                    continue

                observation = self._parse(payload, source)
                if observation is not None:
                    yield observation

                if self._dropped and self._queue.empty():
                    yield self._drop_report()
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _offer(self, payload: bytes, source: str) -> None:
        """Called from the datagram protocol. Never blocks.

        The receive path must not await: back-pressure on a UDP socket is not a
        thing, so the choice is "drop and count" or "grow until OOM".
        """
        if self._allowed and source not in self._allowed:
            return
        try:
            self._queue.put_nowait((payload[:MAX_MESSAGE_BYTES], source))
        except asyncio.QueueFull:
            self._dropped += 1

    def _drop_report(self) -> RawObservation:
        dropped, self._dropped = self._dropped, 0
        return RawObservation(
            kind="sensor.syslog_overflow",
            category="SYSTEM",
            summary=f"Syslog receiver dropped {dropped} messages (queue full)",
            severity="MEDIUM",
            attributes={"dropped": dropped, "queue_size": QUEUE_SIZE},
            uid_seed=f"syslogdrop|{self.name}|{int(utcnow().timestamp() // 60)}",
        )

    # -------------------------------------------------------------- parsing --

    def _parse(self, payload: bytes, source_ip: str) -> RawObservation | None:
        # errors="replace" rather than strict: a device sending non-UTF-8 is
        # common, and dropping the message would lose real information.
        text = payload.decode("utf-8", errors="replace").strip()
        if not text:
            return None

        received_at = utcnow()
        parsed = _parse_rfc5424(text) or _parse_rfc3164(text)
        if parsed is None:
            # Unparseable, but still evidence that something sent something.
            return RawObservation(
                kind="syslog.unparsed",
                category="LOG",
                summary=f"Unparsed syslog message from {source_ip}",
                occurred_at=received_at,
                severity="INFO",
                confidence=50,
                source_ip=source_ip,
                attributes={"format": "unknown"},
                raw=_clean(text),
                uid_seed=f"syslog|{source_ip}|{received_at.isoformat()}|{hash(text) & 0xFFFFFFFF}",
            )

        severity, severity_name = SYSLOG_SEVERITIES.get(parsed["severity"], ("INFO", "unknown"))
        facility = SYSLOG_FACILITIES.get(parsed["facility"], str(parsed["facility"]))
        message = _clean(parsed["message"])[:400]

        return RawObservation(
            kind="syslog.message",
            category="AUTH" if facility in ("auth", "authpriv") else "LOG",
            summary=f"{parsed.get('tag') or facility} on {parsed.get('hostname') or source_ip}: {message}",
            occurred_at=parsed.get("timestamp") or received_at,
            # The sender's claimed severity. Recorded as-is, and named in the
            # attributes as a claim, because a device can say "emergency" about
            # anything it likes.
            severity=severity,
            confidence=100,
            source_ip=source_ip,
            hostname=_clean(parsed.get("hostname") or "")[:255] or None,
            attributes={
                "facility": facility,
                "syslog_severity": severity_name,
                "severity_claimed_by_sender": True,
                "tag": _clean(parsed.get("tag") or "")[:32] or None,
                "process_id": parsed.get("pid"),
                "format": parsed["format"],
                "message": message,
                # A timestamp far from receive time means clock skew or a
                # replayed message; both are worth seeing.
                "clock_skew_seconds": (
                    round((received_at - parsed["timestamp"]).total_seconds(), 1)
                    if parsed.get("timestamp")
                    else None
                ),
            },
            raw=_clean(text),
            uid_seed=f"syslog|{source_ip}|{received_at.isoformat()}|{hash(text) & 0xFFFFFFFF}",
        )


class _SyslogProtocol(asyncio.DatagramProtocol):
    """Bridges asyncio's datagram callbacks into the sensor's queue."""

    def __init__(self, sensor: SyslogSensor) -> None:
        self._sensor = sensor

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._sensor._offer(data, addr[0])

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        logger.warning("syslog_socket_error", extra={"error": exc.__class__.__name__})


def _parse_rfc5424(text: str) -> dict[str, Any] | None:
    match = _RFC5424_RE.match(text)
    if not match:
        return None
    priority = int(match.group("pri"))
    timestamp = _parse_iso(match.group("ts"))
    rest = match.group("rest")
    # Structured data is skipped rather than half-parsed; the message after it
    # is what an operator reads.
    if rest.startswith("["):
        depth = 0
        end_index = 0
        for position, char in enumerate(rest):
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    end_index = position
                    break
        rest = rest[end_index + 1 :].strip()
    return {
        "format": "rfc5424",
        "facility": priority // 8,
        "severity": priority % 8,
        "timestamp": timestamp,
        "hostname": _nil(match.group("host")),
        "tag": _nil(match.group("app")),
        "pid": _nil(match.group("pid")),
        "message": rest,
    }


def _parse_rfc3164(text: str) -> dict[str, Any] | None:
    match = _RFC3164_RE.match(text)
    if not match:
        return None
    priority = int(match.group("pri"))
    rest = match.group("rest")
    tag = pid = None
    tag_match = _TAG_RE.match(rest)
    if tag_match:
        tag = tag_match.group("tag")
        pid = tag_match.group("pid")
        rest = tag_match.group("message")
    return {
        "format": "rfc3164",
        "facility": priority // 8,
        "severity": priority % 8,
        # Deliberately not parsed. RFC 3164 timestamps carry no year and no
        # timezone, so any reconstruction is a guess — and a guessed timestamp
        # on a security event is worse than an honest receive time.
        "timestamp": None,
        "hostname": match.group("host"),
        "tag": tag,
        "pid": pid,
        "message": rest,
    }


def _parse_iso(value: str) -> datetime | None:
    if value == "-":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _nil(value: str) -> str | None:
    """RFC 5424 uses "-" for an absent value."""
    return None if value == "-" else value


def _clean(value: str) -> str:
    """Strip control characters before anything stores or displays this."""
    return _CONTROL_CHARS.sub("", value)
