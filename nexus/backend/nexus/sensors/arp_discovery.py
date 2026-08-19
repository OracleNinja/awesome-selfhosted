"""Device discovery from the kernel's neighbour cache.

What it does
------------
Reads ``/proc/net/arp``, which is the ARP table the Linux kernel already
maintains: for every host on the local segment that this machine has recently
talked to (or that has talked to it), the kernel records IP → MAC. That is real
observed data about real devices, obtained without sending a single packet.

Why start here
--------------
It is the honest default sensor. It needs no elevated privileges, no extra
dependency, and no active probing — it only reads a file the kernel exposes to
every user. So a fresh NEXUS install shows the operator's actual network
immediately, rather than an empty dashboard or, worse, fabricated devices.

What it cannot see, stated plainly
----------------------------------
* Devices this machine has not exchanged traffic with recently. The ARP cache
  is a cache; quiet neighbours age out of it.
* Anything beyond the local segment. Hosts behind a router appear as the
  router's MAC, because that is genuinely all this machine can see at layer 2.
* Traffic. This sensor answers "what is here", not "what is it doing".

Those are limits of the observation point, not of the implementation, and the
UI states them rather than letting an operator assume the inventory is
complete.

Event volume
------------
Polling every 30 seconds and emitting an event per device per poll would
produce tens of thousands of near-identical rows a day and drown everything
interesting. So the driver emits on *change*:

* ``device.discovered`` — first time this MAC has ever been seen. The event id
  is derived from the MAC alone, so a restart cannot re-announce a device the
  database already knows.
* ``device.address_changed`` — the MAC now has a different IP (a new DHCP
  lease, or something more interesting).
* ``device.seen`` — a heartbeat, at most once per device per hour, so an
  operator can tell "still here" from "gone since Tuesday".
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

from nexus.core.config import Settings
from nexus.core.errors import ValidationFailed
from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.sensors.base import AvailabilityReport, RawObservation, Sensor

logger = get_logger(__name__)

ARP_TABLE_PATH = Path("/proc/net/arp")

# Flags column: 0x2 means the entry is complete (a MAC was actually resolved).
# Incomplete entries are pending lookups, not devices, and reporting them would
# invent hosts that may not exist.
ATF_COM = 0x02

NULL_MAC = "00:00:00:00:00:00"
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")

DEFAULT_POLL_SECONDS = 30
DEFAULT_HEARTBEAT_SECONDS = 3600


class ArpDiscoverySensor(Sensor):
    driver: ClassVar[str] = "arp_discovery"
    description: ClassVar[str] = (
        "Discovers devices on the local segment by reading the kernel's ARP "
        "table. Passive: sends no packets and needs no privileges."
    )
    produces_simulated_data: ClassVar[bool] = False
    requires_privileges: ClassVar[bool] = False

    def __init__(self, name: str, config: dict[str, Any], settings: Settings) -> None:
        super().__init__(name, config, settings)
        self._poll_seconds = int(config.get("poll_seconds", DEFAULT_POLL_SECONDS))
        self._heartbeat_seconds = int(config.get("heartbeat_seconds", DEFAULT_HEARTBEAT_SECONDS))
        self._interface = config.get("interface") or None
        # MAC → last IP we reported for it, so a change is detectable without
        # querying the database on every poll.
        self._last_address: dict[str, str] = {}

    # -------------------------------------------------------- availability --

    @classmethod
    def check_availability(cls, config: dict[str, Any], settings: Settings) -> AvailabilityReport:
        if not ARP_TABLE_PATH.exists():
            return AvailabilityReport.unavailable(
                "/proc/net/arp is not present on this system.",
                remedy=(
                    "This driver requires Linux. On macOS or Windows, use a "
                    "sensor that reads from your router instead."
                ),
            )
        try:
            ARP_TABLE_PATH.read_text()
        except OSError as exc:
            return AvailabilityReport.unavailable(
                f"/proc/net/arp cannot be read: {exc.__class__.__name__}.",
                remedy="Check the filesystem permissions on /proc.",
            )
        return AvailabilityReport.available(
            "Reading the kernel neighbour cache. Only devices this host has "
            "recently exchanged traffic with will appear."
        )

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        poll = int(config.get("poll_seconds", DEFAULT_POLL_SECONDS))
        if not 5 <= poll <= 3600:
            raise ValidationFailed("poll_seconds must be between 5 and 3600.")
        heartbeat = int(config.get("heartbeat_seconds", DEFAULT_HEARTBEAT_SECONDS))
        if not 60 <= heartbeat <= 86_400:
            raise ValidationFailed("heartbeat_seconds must be between 60 and 86400.")
        interface = config.get("interface")
        if interface is not None and (
            not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,15}", interface)
        ):
            raise ValidationFailed("interface must be a valid network interface name.")
        return {
            "poll_seconds": poll,
            "heartbeat_seconds": heartbeat,
            **({"interface": interface} if interface else {}),
        }

    # ------------------------------------------------------------ operation --

    async def observations(self) -> AsyncIterator[RawObservation]:
        self._running = True
        try:
            while self._running:
                try:
                    entries = await asyncio.to_thread(self._read_arp_table)
                except OSError as exc:
                    # Do not stop the sensor for a transient read failure. It
                    # reports the problem and tries again next cycle; a sensor
                    # that dies on one bad read is a sensor that is not running
                    # when the problem clears.
                    logger.warning(
                        "arp_table_read_failed",
                        extra={"sensor": self.name, "error": exc.__class__.__name__},
                    )
                    await asyncio.sleep(self._poll_seconds)
                    continue

                for entry in entries:
                    for observation in self._observations_for(entry):
                        yield observation

                await asyncio.sleep(self._poll_seconds)
        finally:
            self._running = False

    def _read_arp_table(self) -> list[dict[str, str]]:
        """Parse /proc/net/arp.

        Format (the header line is skipped)::

            IP address  HW type  Flags  HW address         Mask  Device
            192.168.1.1 0x1      0x2    aa:bb:cc:dd:ee:ff  *     eth0

        Read in a thread because this is blocking file I/O; doing it inline
        would stall the event loop, and /proc reads can block on a busy kernel.
        """
        entries: list[dict[str, str]] = []
        content = ARP_TABLE_PATH.read_text()
        for line in content.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            ip, _hw_type, flags, mac, _mask, device = parts[:6]

            try:
                flag_value = int(flags, 16)
            except ValueError:
                continue
            # Incomplete entries are unresolved lookups, not devices.
            if not flag_value & ATF_COM:
                continue

            mac = mac.lower()
            if mac == NULL_MAC or not _MAC_RE.match(mac):
                continue
            if self._interface and device != self._interface:
                continue

            entries.append({"ip": ip, "mac": mac, "device": device})
        return entries

    def _observations_for(self, entry: dict[str, str]) -> list[RawObservation]:
        """Turn one table row into zero or more events, on change only."""
        mac = entry["mac"]
        ip = entry["ip"]
        now = utcnow()
        in_monitored = self.settings.is_monitored_address(ip)
        attributes = {
            "interface": entry["device"],
            # Recorded rather than used to filter: this sensor is passive, and
            # the monitored-networks boundary governs *actions*, not
            # observation. Detection can use the flag for context.
            "in_monitored_network": in_monitored,
            "discovery_method": "arp_cache",
        }

        previous = self._last_address.get(mac)
        self._last_address[mac] = ip
        observations: list[RawObservation] = []

        if previous is None:
            observations.append(
                RawObservation(
                    kind="device.discovered",
                    category="DISCOVERY",
                    summary=f"Device {mac} discovered at {ip}",
                    occurred_at=now,
                    severity="INFO",
                    # The ARP cache resolved this MAC, so the pairing was
                    # actually observed rather than inferred.
                    confidence=100,
                    source_ip=ip,
                    mac_address=mac,
                    attributes=attributes,
                    # No timestamp in the seed: a device is discovered once,
                    # ever. A restart re-reads the same table and produces the
                    # same id, which the unique index discards.
                    uid_seed=f"discovered|{mac}",
                )
            )
        elif previous != ip:
            observations.append(
                RawObservation(
                    kind="device.address_changed",
                    category="DISCOVERY",
                    summary=f"Device {mac} moved from {previous} to {ip}",
                    occurred_at=now,
                    severity="LOW",
                    confidence=100,
                    source_ip=ip,
                    mac_address=mac,
                    attributes={**attributes, "previous_ip": previous},
                    uid_seed=f"addrchange|{mac}|{previous}|{ip}|{now:%Y-%m-%dT%H:%M}",
                )
            )

        # Heartbeat, bucketed so at most one lands per device per window.
        bucket = int(now.timestamp() // self._heartbeat_seconds)
        observations.append(
            RawObservation(
                kind="device.seen",
                category="DISCOVERY",
                summary=f"Device {mac} present at {ip}",
                occurred_at=now,
                severity="INFO",
                confidence=100,
                source_ip=ip,
                mac_address=mac,
                attributes=attributes,
                uid_seed=f"seen|{mac}|{bucket}",
            )
        )
        return observations
