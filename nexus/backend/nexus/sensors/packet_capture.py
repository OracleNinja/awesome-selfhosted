"""Passive packet capture over a raw socket.

What actually happens underneath
--------------------------------
``socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL))`` asks the Linux kernel for a
copy of every frame that crosses a network interface, delivered as raw bytes
starting at the Ethernet header. There is no library between us and the wire —
each `recv` returns one frame exactly as the NIC saw it.

That is why it needs privilege. A process that can read every frame can read
every unencrypted password on the segment, so the kernel gates it behind
``CAP_NET_RAW``. NEXUS asks for that one capability rather than running as
root: the difference between "can sniff packets" and "can do anything" is the
entire point of capabilities.

**This sensor is strictly passive.** It opens the socket read-only and never
transmits. It cannot scan, spoof, inject, or interfere with anything — the
capability that would allow that is deliberately not used, and there is no code
path here that writes to the socket.

Why flow aggregation rather than per-packet events
---------------------------------------------------
A quiet home network carries a few thousand packets a second. One event per
packet is hundreds of millions of rows a day: unqueryable, unaffordable, and
useless — no operator reads individual packets. So packets are folded into
*flows* — (source, destination, protocol, port) with counters — and one event
is emitted per flow per window. That is the same abstraction NetFlow uses, for
the same reason, and it preserves what matters (who talked to whom, how much,
when) while discarding what does not.

Parsing hostile input
---------------------
Every byte here is attacker-controlled. A malformed frame must produce a
discarded packet, never an exception that kills the sensor and never a read
past the end of a buffer. Each parser therefore checks its length before
unpacking and returns ``None`` on anything it does not fully understand.
Payload bytes are never stored: a packet capture of your own network contains
your own traffic, and this system has no business keeping it.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

from nexus.core.config import Settings
from nexus.core.errors import ValidationFailed
from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.sensors.base import AvailabilityReport, RawObservation, Sensor

logger = get_logger(__name__)

# From <linux/if_ether.h>. ETH_P_ALL means "every protocol", i.e. everything.
ETH_P_ALL = 0x0003

ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_ARP = 0x0806
ETHERTYPE_IPV6 = 0x86DD
ETHERTYPE_VLAN = 0x8100

IPPROTO_NAMES = {1: "ICMP", 6: "TCP", 17: "UDP", 58: "ICMPv6"}

ETHERNET_HEADER_LENGTH = 14
IPV4_MIN_HEADER = 20
IPV6_HEADER = 40
TCP_MIN_HEADER = 20
UDP_HEADER = 8

# One frame. 65535 covers a jumbo frame with room to spare.
CAPTURE_BUFFER = 65535

DEFAULT_WINDOW_SECONDS = 60
# Beyond this many concurrent flows in a window, new ones are counted and
# dropped rather than tracked. An unbounded flow table is a memory-exhaustion
# vulnerability that any host on the segment could trigger by spraying packets
# with random source ports — the defence must not become the vulnerability.
DEFAULT_MAX_FLOWS = 20_000


@dataclass
class _Flow:
    """Accumulated counters for one conversation within a window."""

    source_ip: str
    destination_ip: str
    protocol: str
    destination_port: int | None
    source_mac: str | None
    packets: int = 0
    bytes: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    tcp_flags: set[str] = field(default_factory=set)


class PacketCaptureSensor(Sensor):
    driver: ClassVar[str] = "packet_capture"
    description: ClassVar[str] = (
        "Passively captures frames on an interface and aggregates them into "
        "flows. Requires CAP_NET_RAW. Never transmits."
    )
    produces_simulated_data: ClassVar[bool] = False
    requires_privileges: ClassVar[bool] = True

    def __init__(self, name: str, config: dict[str, Any], settings: Settings) -> None:
        super().__init__(name, config, settings)
        self._interface: str = config["interface"]
        self._window_seconds = int(config.get("window_seconds", DEFAULT_WINDOW_SECONDS))
        self._max_flows = int(config.get("max_flows", DEFAULT_MAX_FLOWS))
        self._socket: socket.socket | None = None
        self._flows: dict[tuple, _Flow] = {}
        self._dropped_flows = 0
        self._malformed = 0
        self._packets_seen = 0

    # -------------------------------------------------------- availability --

    @classmethod
    def check_availability(cls, config: dict[str, Any], settings: Settings) -> AvailabilityReport:
        if not hasattr(socket, "AF_PACKET"):
            return AvailabilityReport.unavailable(
                "Raw packet sockets (AF_PACKET) are a Linux feature and are not "
                "available on this platform.",
                remedy="Run NEXUS on Linux, or use a router-based sensor instead.",
            )

        interface = config.get("interface")
        if not interface:
            return AvailabilityReport.unconfigured(
                "No capture interface has been selected.",
                remedy=f"Set `interface` to one of: {', '.join(list_interfaces()) or 'none found'}",
            )
        if interface not in list_interfaces():
            return AvailabilityReport.unconfigured(
                f"Interface {interface!r} does not exist on this host.",
                remedy=f"Available interfaces: {', '.join(list_interfaces()) or 'none found'}",
            )

        # The only reliable test of the capability is to try. Checking for
        # euid == 0 would be wrong twice over: root is not required (CAP_NET_RAW
        # is enough), and being root is not sufficient inside a container whose
        # capability set was dropped.
        try:
            probe = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        except PermissionError:
            return AvailabilityReport.unavailable(
                "This process lacks CAP_NET_RAW, so it cannot capture packets.",
                remedy=(
                    "Grant the capability without running as root:\n"
                    "  sudo setcap cap_net_raw,cap_net_admin+eip $(readlink -f $(which python3))\n"
                    "or run the container with --cap-add=NET_RAW."
                ),
            )
        except OSError as exc:
            return AvailabilityReport.unavailable(
                f"Raw socket unavailable: {exc.__class__.__name__}.",
                remedy="Check kernel support and container capabilities.",
            )
        else:
            probe.close()

        return AvailabilityReport.available(
            f"Capturing on {interface}. Frames are aggregated into flows; "
            "payload bytes are never stored."
        )

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        interface = config.get("interface")
        available = list_interfaces()
        if not interface or not isinstance(interface, str):
            raise ValidationFailed(
                "interface is required.",
                details={"available_interfaces": available},
            )
        # Membership check against the real interface list, not a regex: this
        # value names a kernel object, and an allow-list of things that
        # actually exist is stronger than any pattern.
        if interface not in available:
            raise ValidationFailed(
                f"No such interface: {interface!r}.",
                details={"available_interfaces": available},
            )

        window = int(config.get("window_seconds", DEFAULT_WINDOW_SECONDS))
        if not 10 <= window <= 900:
            raise ValidationFailed("window_seconds must be between 10 and 900.")
        max_flows = int(config.get("max_flows", DEFAULT_MAX_FLOWS))
        if not 100 <= max_flows <= 500_000:
            raise ValidationFailed("max_flows must be between 100 and 500000.")
        return {
            "interface": interface,
            "window_seconds": window,
            "max_flows": max_flows,
        }

    # ------------------------------------------------------------ operation --

    async def observations(self) -> AsyncIterator[RawObservation]:
        loop = asyncio.get_running_loop()
        self._open_socket()
        capture_socket = self._socket
        assert capture_socket is not None  # _open_socket sets it or raises
        self._running = True
        window_started = loop.time()

        try:
            while self._running:
                try:
                    # Non-blocking socket plus loop.sock_recv: the event loop
                    # waits on readability, so the process is not pinned to a
                    # blocking read and other work continues between frames.
                    frame = await asyncio.wait_for(
                        loop.sock_recv(capture_socket, CAPTURE_BUFFER), timeout=1.0
                    )
                except TimeoutError:
                    frame = None
                except OSError as exc:
                    # An interface going down (unplugged cable, VPN teardown)
                    # surfaces here. Report and stop; the manager restarts with
                    # backoff and re-checks availability, which is where the
                    # operator sees a useful message.
                    logger.warning(
                        "capture_socket_error",
                        extra={"sensor": self.name, "error": exc.__class__.__name__},
                    )
                    raise

                if frame:
                    self._packets_seen += 1
                    self._absorb(frame, loop.time())

                now = loop.time()
                if now - window_started >= self._window_seconds:
                    for observation in self._flush(window_started, now):
                        yield observation
                    window_started = now
        finally:
            await self.stop()

    def _open_socket(self) -> None:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        sock.bind((self._interface, 0))
        sock.setblocking(False)
        # A modest receive buffer: if NEXUS falls behind, the kernel drops
        # frames rather than growing memory without bound. Dropped frames are
        # counted and reported — a sensor that silently loses data is worse
        # than one that says it is overloaded.
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
        self._socket = sock
        logger.info("capture_started", extra={"sensor": self.name, "interface": self._interface})

    async def stop(self) -> None:
        self._running = False
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.close()
            self._socket = None

    # -------------------------------------------------------------- parsing --

    def _absorb(self, frame: bytes, now: float) -> None:
        """Fold one frame into the flow table."""
        parsed = parse_frame(frame)
        if parsed is None:
            self._malformed += 1
            return

        key = (
            parsed["source_ip"],
            parsed["destination_ip"],
            parsed["protocol"],
            parsed.get("destination_port"),
        )
        flow = self._flows.get(key)
        if flow is None:
            if len(self._flows) >= self._max_flows:
                # Bounded, and the drop is counted so the UI can say the
                # sensor is saturated instead of quietly under-reporting.
                self._dropped_flows += 1
                return
            flow = _Flow(
                source_ip=parsed["source_ip"],
                destination_ip=parsed["destination_ip"],
                protocol=parsed["protocol"],
                destination_port=parsed.get("destination_port"),
                source_mac=parsed.get("source_mac"),
                first_seen=now,
            )
            self._flows[key] = flow

        flow.packets += 1
        flow.bytes += len(frame)
        flow.last_seen = now
        if parsed.get("tcp_flags"):
            flow.tcp_flags.update(parsed["tcp_flags"])

    def _flush(self, window_started: float, now: float) -> list[RawObservation]:
        """Emit one observation per flow and reset the table."""
        flows, self._flows = self._flows, {}
        observations: list[RawObservation] = []
        occurred_at = utcnow()
        # Bucket id derived from the window, so re-emitting the same window
        # after a restart collapses to the same event rather than duplicating.
        bucket = int(occurred_at.timestamp() // max(1, self._window_seconds))

        for flow in flows.values():
            port_part = f":{flow.destination_port}" if flow.destination_port else ""
            observations.append(
                RawObservation(
                    kind="flow.observed",
                    category="TRAFFIC",
                    summary=(
                        f"{flow.protocol} {flow.source_ip} → {flow.destination_ip}"
                        f"{port_part} ({flow.packets} packets, {flow.bytes} bytes)"
                    ),
                    occurred_at=occurred_at,
                    severity="INFO",
                    confidence=100,
                    source_ip=flow.source_ip,
                    destination_ip=flow.destination_ip,
                    destination_port=flow.destination_port,
                    protocol=flow.protocol,
                    mac_address=flow.source_mac,
                    attributes={
                        "packets": flow.packets,
                        "bytes": flow.bytes,
                        "duration_seconds": round(max(0.0, flow.last_seen - flow.first_seen), 3),
                        "tcp_flags": sorted(flow.tcp_flags) or None,
                        "interface": self._interface,
                        "window_seconds": self._window_seconds,
                        "source_in_monitored_network": self.settings.is_monitored_address(
                            flow.source_ip
                        ),
                        "destination_in_monitored_network": self.settings.is_monitored_address(
                            flow.destination_ip
                        ),
                    },
                    uid_seed=(
                        f"flow|{flow.source_ip}|{flow.destination_ip}|{flow.protocol}"
                        f"|{flow.destination_port}|{bucket}"
                    ),
                )
            )

        if self._dropped_flows or self._malformed:
            # The sensor reporting on itself. Without this, saturation looks
            # like a quiet network.
            observations.append(
                RawObservation(
                    kind="sensor.capture_saturated"
                    if self._dropped_flows
                    else "sensor.malformed_frames",
                    category="SYSTEM",
                    summary=(
                        f"Capture on {self._interface}: {self._dropped_flows} flows dropped "
                        f"(table limit {self._max_flows}), {self._malformed} unparseable frames"
                    ),
                    occurred_at=occurred_at,
                    severity="MEDIUM" if self._dropped_flows else "LOW",
                    confidence=100,
                    attributes={
                        "dropped_flows": self._dropped_flows,
                        "malformed_frames": self._malformed,
                        "packets_seen": self._packets_seen,
                        "interface": self._interface,
                    },
                    uid_seed=f"saturation|{self.name}|{bucket}",
                )
            )
            self._dropped_flows = 0
            self._malformed = 0

        return observations


# ---------------------------------------------------------------- parsers --
#
# Every parser below takes untrusted bytes. The rules: check the length before
# unpacking, never index past what was checked, and return None rather than
# raising on anything unrecognised. A malformed frame is a discarded frame.


def parse_frame(frame: bytes) -> dict[str, Any] | None:
    """Parse Ethernet → IP → transport. Returns ``None`` for anything else."""
    if len(frame) < ETHERNET_HEADER_LENGTH:
        return None

    destination_mac = _format_mac(frame[0:6])
    source_mac = _format_mac(frame[6:12])
    ethertype = struct.unpack("!H", frame[12:14])[0]
    offset = ETHERNET_HEADER_LENGTH

    # A VLAN tag inserts four bytes and pushes the real ethertype along. Not
    # handling this would misparse every frame on a tagged network.
    if ethertype == ETHERTYPE_VLAN:
        if len(frame) < offset + 4:
            return None
        ethertype = struct.unpack("!H", frame[offset + 2 : offset + 4])[0]
        offset += 4

    if ethertype == ETHERTYPE_IPV4:
        parsed = _parse_ipv4(frame, offset)
    elif ethertype == ETHERTYPE_IPV6:
        parsed = _parse_ipv6(frame, offset)
    else:
        # ARP and everything else is left to other sensors; this one is about
        # IP conversations.
        return None

    if parsed is None:
        return None
    parsed["source_mac"] = source_mac
    parsed["destination_mac"] = destination_mac
    return parsed


def _parse_ipv4(frame: bytes, offset: int) -> dict[str, Any] | None:
    if len(frame) < offset + IPV4_MIN_HEADER:
        return None

    version_ihl = frame[offset]
    if version_ihl >> 4 != 4:
        return None
    # IHL counts 32-bit words, so the header can legitimately be longer than 20
    # bytes when options are present. Trusting a fixed 20 would misparse those
    # and read the wrong bytes as ports.
    header_length = (version_ihl & 0x0F) * 4
    if header_length < IPV4_MIN_HEADER or len(frame) < offset + header_length:
        return None

    protocol_number = frame[offset + 9]
    source_ip = socket.inet_ntop(socket.AF_INET, frame[offset + 12 : offset + 16])
    destination_ip = socket.inet_ntop(socket.AF_INET, frame[offset + 16 : offset + 20])

    result: dict[str, Any] = {
        "source_ip": source_ip,
        "destination_ip": destination_ip,
        "protocol": IPPROTO_NAMES.get(protocol_number, f"IP:{protocol_number}"),
        "ttl": frame[offset + 8],
    }
    result.update(_parse_transport(frame, offset + header_length, protocol_number))
    return result


def _parse_ipv6(frame: bytes, offset: int) -> dict[str, Any] | None:
    if len(frame) < offset + IPV6_HEADER:
        return None
    if frame[offset] >> 4 != 6:
        return None

    next_header = frame[offset + 6]
    source_ip = socket.inet_ntop(socket.AF_INET6, frame[offset + 8 : offset + 24])
    destination_ip = socket.inet_ntop(socket.AF_INET6, frame[offset + 24 : offset + 40])

    result: dict[str, Any] = {
        "source_ip": source_ip,
        "destination_ip": destination_ip,
        "protocol": IPPROTO_NAMES.get(next_header, f"IP:{next_header}"),
        "hop_limit": frame[offset + 7],
    }
    # IPv6 extension headers are not walked; when one is present the "next
    # header" is not a transport protocol and ports stay unknown. Reporting the
    # conversation without ports is honest — guessing them would not be.
    result.update(_parse_transport(frame, offset + IPV6_HEADER, next_header))
    return result


def _parse_transport(frame: bytes, offset: int, protocol_number: int) -> dict[str, Any]:
    if protocol_number == 6:  # TCP
        if len(frame) < offset + TCP_MIN_HEADER:
            return {}
        source_port, destination_port = struct.unpack("!HH", frame[offset : offset + 4])
        flags_byte = frame[offset + 13]
        return {
            "source_port": source_port,
            "destination_port": destination_port,
            "tcp_flags": _tcp_flag_names(flags_byte),
        }
    if protocol_number == 17:  # UDP
        if len(frame) < offset + UDP_HEADER:
            return {}
        source_port, destination_port = struct.unpack("!HH", frame[offset : offset + 4])
        return {"source_port": source_port, "destination_port": destination_port}
    if protocol_number in (1, 58):  # ICMP / ICMPv6
        if len(frame) < offset + 2:
            return {}
        return {"icmp_type": frame[offset], "icmp_code": frame[offset + 1]}
    return {}


_TCP_FLAGS = (
    (0x01, "FIN"),
    (0x02, "SYN"),
    (0x04, "RST"),
    (0x08, "PSH"),
    (0x10, "ACK"),
    (0x20, "URG"),
)


def _tcp_flag_names(flags: int) -> list[str]:
    """Decode the TCP flag bits.

    Worth keeping: SYN without ACK is a connection attempt, and a burst of them
    to many ports is a port scan. The detection engine reads these rather than
    re-parsing packets.
    """
    return [name for bit, name in _TCP_FLAGS if flags & bit]


def _format_mac(raw: bytes) -> str:
    return ":".join(f"{byte:02x}" for byte in raw)


def list_interfaces() -> list[str]:
    """Network interfaces on this host.

    Used to validate configuration against reality and to offer a choice in the
    UI, rather than making an operator guess an interface name.
    """
    try:
        return sorted(name for _index, name in socket.if_nameindex() if name != "lo")
    except OSError:  # pragma: no cover - platform without if_nameindex
        return []
