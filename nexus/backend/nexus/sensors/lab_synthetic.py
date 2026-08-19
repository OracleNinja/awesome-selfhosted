"""Laboratory event generator.

This is the *only* driver in NEXUS that produces data nobody observed, and it
exists for reasons the project takes seriously:

* A detection rule that has never fired is a rule nobody has tested. Waiting
  for a real port scan to validate the port-scan rule is not a plan.
* Incident-response practice needs an incident.
* Nobody should have to attack their own network to see whether the dashboard
  lights up.

The rules that keep it honest
-----------------------------
1. ``produces_simulated_data = True``, so **every** event it creates is stamped
   ``is_simulation`` in the database. The flag comes from the driver's identity,
   not from a parameter a caller could forget.
2. Addresses come from ranges reserved for documentation and examples
   (RFC 5737: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24). They can never
   collide with a real device on the operator's network, so a simulated host
   can never be confused for a real one — and any action taken against one goes
   nowhere.
3. Scenarios are declarative and finite. This generator does not run
   continuously pretending to be a network; it plays a defined script and
   stops.
4. It refuses to start unless the sensor row is explicitly marked as a
   simulation sensor, so it cannot be configured to masquerade as a real one.

What it is not
--------------
It is not an attack tool. It writes rows to a database that describe traffic
that never existed. It sends no packets, touches no host, and has no network
access of any kind.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from nexus.core.config import Settings
from nexus.core.errors import ValidationFailed
from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.sensors.base import AvailabilityReport, RawObservation, Sensor

logger = get_logger(__name__)

# RFC 5737 documentation ranges: reserved by the IETF specifically so they can
# appear in examples without ever routing anywhere or colliding with a real
# host. Using 192.168.x would risk a simulated event naming a real device.
LAB_NETWORK = "192.0.2."
LAB_EXTERNAL = "198.51.100."
LAB_SECOND = "203.0.113."

# Locally-administered MAC prefix (the 0x02 bit), which cannot be a real
# manufacturer-assigned address.
LAB_MAC_PREFIX = "02:00:5e"

SCENARIOS: dict[str, str] = {
    "quiet_network": "Ordinary background traffic with nothing of interest.",
    "port_scan": "One host sweeping many ports on another — the classic scan signature.",
    "ssh_brute_force": "Repeated failed SSH authentications from a single source.",
    "new_device": "A previously unseen device joining the network.",
    "data_exfiltration": "A sustained outbound transfer far above the host's normal volume.",
    "dns_tunnelling": "Abnormally large and frequent DNS queries to one domain.",
}


class LabSyntheticSensor(Sensor):
    driver: ClassVar[str] = "lab_synthetic"
    description: ClassVar[str] = (
        "SIMULATION ONLY. Generates laboratory events for testing detections "
        "and practising response. Every event it produces is flagged as "
        "simulated and uses reserved documentation addresses."
    )
    produces_simulated_data: ClassVar[bool] = True
    requires_privileges: ClassVar[bool] = False

    def __init__(self, name: str, config: dict[str, Any], settings: Settings) -> None:
        super().__init__(name, config, settings)
        self._scenario = config.get("scenario", "quiet_network")
        self._rate = float(config.get("events_per_second", 2.0))
        self._max_events = int(config.get("max_events", 200))
        # Seeded so a scenario replays identically. A test that cannot be
        # reproduced cannot be debugged, and a demo that differs every run
        # cannot be discussed.
        self._random = random.Random(config.get("seed", 1337))  # noqa: S311 - not cryptographic
        self._emitted = 0
        # Distinguishes one run from another so replays do not collide on the
        # deduplication index.
        self._run_id = utcnow().strftime("%Y%m%dT%H%M%S")

    # -------------------------------------------------------- availability --

    @classmethod
    def check_availability(cls, config: dict[str, Any], settings: Settings) -> AvailabilityReport:
        scenario = config.get("scenario", "quiet_network")
        if scenario not in SCENARIOS:
            return AvailabilityReport.unconfigured(
                f"Unknown scenario {scenario!r}.",
                remedy=f"Choose one of: {', '.join(sorted(SCENARIOS))}",
            )
        return AvailabilityReport.available(
            f"SIMULATION: will generate '{scenario}' laboratory events. "
            "Nothing here is observed from a real network."
        )

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        scenario = config.get("scenario", "quiet_network")
        if scenario not in SCENARIOS:
            raise ValidationFailed(
                f"Unknown scenario {scenario!r}.",
                details={"available_scenarios": sorted(SCENARIOS)},
            )
        rate = float(config.get("events_per_second", 2.0))
        if not 0.1 <= rate <= 100:
            raise ValidationFailed("events_per_second must be between 0.1 and 100.")
        max_events = int(config.get("max_events", 200))
        if not 1 <= max_events <= 100_000:
            raise ValidationFailed("max_events must be between 1 and 100000.")
        return {
            "scenario": scenario,
            "events_per_second": rate,
            "max_events": max_events,
            "seed": int(config.get("seed", 1337)),
        }

    # ------------------------------------------------------------ operation --

    async def observations(self) -> AsyncIterator[RawObservation]:
        self._running = True
        interval = 1.0 / self._rate
        generator = getattr(self, f"_scenario_{self._scenario}")

        logger.info(
            "lab_scenario_started",
            extra={"sensor": self.name, "scenario": self._scenario, "simulation": True},
        )
        try:
            for observation in generator():
                if not self._running or self._emitted >= self._max_events:
                    break
                self._emitted += 1
                # Belt and braces: the ingestion layer stamps is_simulation from
                # the driver, and the attribute says it again in the payload an
                # operator reads.
                observation.attributes["simulation"] = True
                observation.attributes["scenario"] = self._scenario
                yield observation
                await asyncio.sleep(interval)
        finally:
            self._running = False
            logger.info(
                "lab_scenario_finished",
                extra={"sensor": self.name, "emitted": self._emitted},
            )

    # ------------------------------------------------------------ scenarios --

    def _host(self, octet: int) -> str:
        return f"{LAB_NETWORK}{octet}"

    def _mac(self, octet: int) -> str:
        return f"{LAB_MAC_PREFIX}:00:00:{octet:02x}"

    def _uid(self, label: str, index: int) -> str:
        return f"lab|{self._run_id}|{self._scenario}|{label}|{index}"

    def _scenario_quiet_network(self):
        """Background traffic: the baseline a detection must not fire on."""
        for index in range(self._max_events):
            source = self._host(self._random.randint(10, 40))
            yield RawObservation(
                kind="flow.observed",
                category="TRAFFIC",
                summary=f"[SIMULATION] TCP {source} → {LAB_EXTERNAL}20:443 (routine)",
                severity="INFO",
                source_ip=source,
                destination_ip=f"{LAB_EXTERNAL}20",
                destination_port=443,
                protocol="TCP",
                attributes={
                    "packets": self._random.randint(5, 60),
                    "bytes": self._random.randint(500, 40_000),
                },
                uid_seed=self._uid("quiet", index),
            )

    def _scenario_port_scan(self):
        """One source touching many ports on one target in quick succession."""
        scanner = self._host(66)
        target = self._host(20)
        for index, port in enumerate(
            [
                21,
                22,
                23,
                25,
                53,
                80,
                110,
                135,
                139,
                143,
                443,
                445,
                993,
                995,
                1433,
                3306,
                3389,
                5432,
                5900,
                8080,
                8443,
            ]
        ):
            yield RawObservation(
                kind="flow.observed",
                category="TRAFFIC",
                summary=f"[SIMULATION] TCP {scanner} → {target}:{port} (SYN, no reply)",
                severity="INFO",
                confidence=100,
                source_ip=scanner,
                destination_ip=target,
                destination_port=port,
                protocol="TCP",
                attributes={
                    "packets": 1,
                    "bytes": 60,
                    # The signature the detection rule keys on: a connection
                    # attempt with no completed handshake.
                    "tcp_flags": ["SYN"],
                },
                uid_seed=self._uid("scan", index),
            )

    def _scenario_ssh_brute_force(self):
        """Repeated authentication failures from one source."""
        attacker = f"{LAB_EXTERNAL}77"
        target = self._host(20)
        for index in range(40):
            yield RawObservation(
                kind="syslog.message",
                category="AUTH",
                summary=(
                    f"[SIMULATION] sshd on {target}: Failed password for invalid "
                    f"user admin from {attacker} port {40000 + index}"
                ),
                severity="MEDIUM",
                source_ip=attacker,
                destination_ip=target,
                destination_port=22,
                protocol="TCP",
                attributes={
                    "facility": "authpriv",
                    "tag": "sshd",
                    "auth_result": "failure",
                    "username": "admin",
                },
                uid_seed=self._uid("ssh", index),
            )

    def _scenario_new_device(self):
        """An unfamiliar device appearing on the network."""
        octet = 200
        yield RawObservation(
            kind="device.discovered",
            category="DISCOVERY",
            summary=f"[SIMULATION] Device {self._mac(octet)} discovered at {self._host(octet)}",
            severity="LOW",
            source_ip=self._host(octet),
            mac_address=self._mac(octet),
            attributes={"discovery_method": "simulated", "in_monitored_network": False},
            uid_seed=self._uid("newdev", octet),
        )
        for index in range(5):
            yield RawObservation(
                kind="flow.observed",
                category="TRAFFIC",
                summary=f"[SIMULATION] TCP {self._host(octet)} → {LAB_EXTERNAL}30:443",
                severity="INFO",
                source_ip=self._host(octet),
                destination_ip=f"{LAB_EXTERNAL}30",
                destination_port=443,
                protocol="TCP",
                mac_address=self._mac(octet),
                attributes={"packets": 20, "bytes": 15_000},
                uid_seed=self._uid("newdevflow", index),
            )

    def _scenario_data_exfiltration(self):
        """A sustained outbound transfer far above the host's normal volume."""
        source = self._host(31)
        destination = f"{LAB_SECOND}99"
        for index in range(30):
            yield RawObservation(
                kind="flow.observed",
                category="TRAFFIC",
                summary=(
                    f"[SIMULATION] TCP {source} → {destination}:443 "
                    f"({120_000_000 // 30} bytes outbound)"
                ),
                severity="INFO",
                source_ip=source,
                destination_ip=destination,
                destination_port=443,
                protocol="TCP",
                attributes={
                    "packets": 3000,
                    "bytes": 120_000_000 // 30,
                    "direction": "outbound",
                },
                uid_seed=self._uid("exfil", index),
            )

    def _scenario_dns_tunnelling(self):
        """Large, frequent DNS queries to one domain — a covert channel."""
        source = self._host(45)
        for index in range(50):
            label = "".join(self._random.choices("abcdef0123456789", k=48))
            yield RawObservation(
                kind="flow.observed",
                category="TRAFFIC",
                summary=f"[SIMULATION] UDP {source} → {LAB_EXTERNAL}53:53 (DNS, {len(label)}-byte label)",
                severity="INFO",
                source_ip=source,
                destination_ip=f"{LAB_EXTERNAL}53",
                destination_port=53,
                protocol="UDP",
                attributes={
                    "packets": 2,
                    "bytes": 300 + len(label),
                    "dns_query_length": len(label),
                    "dns_domain": "tunnel.example.invalid",
                },
                uid_seed=self._uid("dns", index),
            )
