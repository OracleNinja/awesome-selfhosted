"""Rule: a monitored device reaching an external endpoint on an unexpected port.

What "unexpected" means here, precisely
---------------------------------------
Three conditions, all observable, none inferred:

1. the source address is inside a network the operator declared as theirs;
2. the destination address is outside every such network;
3. the destination port is not on the configured allow-list.

That is the whole rule. It says a device talked to something outside, on a port
that is not one of the handful ordinary devices use. It does **not** say the
endpoint is malicious, that the traffic is command-and-control, or that the
device is compromised — nothing in this system has evidence for any of those
claims. NEXUS ships no threat-intelligence feed, so no address here is ever
compared against a reputation list; if one is added later it will be a separate,
clearly-attributed source of evidence.

Why this rule refuses to run when networks are not configured
-------------------------------------------------------------
Conditions 1 and 2 are meaningless without ``NEXUS_MONITORED_NETWORKS``. With an
empty list, ``Settings.is_monitored_address`` returns ``False`` for everything
(it fails closed, deliberately), so *every* observation would look like traffic
from an unmonitored source to an unmonitored destination. The rule would either
fire on all traffic or on none, and both would be lies about the network.

It therefore reports ``NOT_CONFIGURED`` for the real-network scope until the
operator declares their networks — an explicit, visible state with a remedy,
never an empty result that reads as "nothing found".

Laboratory scope
----------------
Simulated observations use RFC 5737 documentation addresses, which by design
never appear on a real network. Those blocks are the laboratory's own declared
scope, configured separately, so the rule is meaningful for laboratory data even
on a deployment that has not declared its real networks — and the two scopes are
evaluated independently, so a laboratory scenario can never produce a finding
about the real network.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from nexus.core.config import Settings
from nexus.detection.base import (
    Candidate,
    DetectionContext,
    Detector,
    DetectorAvailability,
)
from nexus.detection.config import DetectionConfig
from nexus.detection.observation import Observation

# RFC 5737 documentation ranges: the address space the laboratory driver uses,
# guaranteed by standard never to be routed on a real network.
SIMULATION_NETWORKS = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")

MAX_EVIDENCE_LINKS = 10


class UnexpectedCommunicationDetector(Detector):
    id = "unexpected_communication"
    detection_type = "UNEXPECTED_COMMUNICATION"
    version = 1
    description = (
        "A device inside a monitored network communicating with an external endpoint "
        "on a port outside the configured allow-list."
    )

    def _enabled(self, config: DetectionConfig) -> bool:
        return config.rules.unexpected_communication.enabled

    def availability(
        self, config: DetectionConfig, settings: Settings, *, is_simulation: bool
    ) -> DetectorAvailability:
        base = super().availability(config, settings, is_simulation=is_simulation)
        if not base.can_run:
            return base
        if is_simulation:
            # The laboratory's scope is defined by this module, not by operator
            # configuration, so it is always available.
            return DetectorAvailability(state="ACTIVE")
        if not settings.monitored_networks:
            return DetectorAvailability(
                state="NOT_CONFIGURED",
                detail=(
                    "No monitored networks are declared, so this rule cannot tell "
                    "internal traffic from external traffic."
                ),
                remedy="Set NEXUS_MONITORED_NETWORKS to the CIDR blocks you own.",
            )
        return DetectorAvailability(state="ACTIVE")

    async def evaluate(self, context: DetectionContext) -> list[Candidate]:
        rule = context.config.rules.unexpected_communication
        networks = _scope_networks(context.settings, is_simulation=context.is_simulation)
        if not networks:
            # Availability already reports this; returning nothing here is the
            # correct behaviour rather than a silent failure, because the engine
            # never calls a detector whose availability is not ACTIVE.
            return []

        allowed_ports = set(rule.allowed_destination_ports)
        grouped: dict[tuple[Any, str, int, str], list[Observation]] = {}

        for item in context.with_device():
            if not item.source_ip or not item.destination_ip or item.destination_port is None:
                continue
            if not _within(item.source_ip, networks):
                continue
            if rule.ignore_internal_destinations and _within(item.destination_ip, networks):
                continue
            if item.destination_port in allowed_ports:
                continue

            key = (
                item.device_id,
                item.destination_ip,
                item.destination_port,
                item.protocol or "UNKNOWN",
            )
            grouped.setdefault(key, []).append(item)

        candidates: list[Candidate] = []
        for key, observations in sorted(
            grouped.items(), key=lambda entry: (str(entry[0][0]), entry[0][1], entry[0][2])
        ):
            candidates.append(self._build(context, key, observations))
        return candidates

    def _build(
        self,
        context: DetectionContext,
        key: tuple[Any, str, int, str],
        observations: list[Observation],
    ) -> Candidate:
        device_id, destination, port, protocol = key
        ordered = sorted(observations, key=lambda item: (item.occurred_at, item.event_id))
        trigger = ordered[0]
        rule = context.config.rules.unexpected_communication

        total_bytes = sum(
            int(item.attributes.get("bytes", 0) or 0)
            for item in ordered
            if isinstance(item.attributes.get("bytes"), (int, float))
        )

        evidence: dict[str, Any] = {
            "destination_ip": destination,
            "destination_port": port,
            "protocol": protocol,
            "flows_in_batch": len(ordered),
            "observed_bytes": total_bytes or None,
            "allowed_destination_ports": list(rule.allowed_destination_ports),
            "monitored_networks": [
                str(network)
                for network in _scope_networks(
                    context.settings, is_simulation=context.is_simulation
                )
            ],
            "scope": "SIMULATION" if context.is_simulation else "REAL_NETWORK",
            "observations": [item.evidence_ref() for item in ordered[:MAX_EVIDENCE_LINKS]],
            "provenance": trigger.provenance.as_dict(),
        }

        explanation = (
            f"This device communicated with {destination} on port {port}/{protocol}, which is "
            f"outside the monitored networks and not on the configured allow-list of "
            f"{sorted(rule.allowed_destination_ports)}. Observed {len(ordered)} time(s) in this "
            f"batch, first at {trigger.occurred_at.isoformat()} by sensor "
            f"'{trigger.provenance.sensor_name}'. This finding reports an unexpected "
            f"destination; it is not an assessment of that endpoint's reputation, and NEXUS "
            f"holds no threat-intelligence data about it."
        )

        links: list[tuple[int, str]] = [(trigger.event_id, "TRIGGER")]
        links.extend((item.event_id, "SUPPORTING") for item in ordered[1 : 1 + MAX_EVIDENCE_LINKS])

        return Candidate(
            detector=self.id,
            rule_version=self.version,
            detection_type=self.detection_type,
            fingerprint_parts=("device", str(device_id), destination, str(port), protocol),
            # Worth investigating, not worth waking someone: the observation is
            # certain, its interpretation depends entirely on how complete the
            # operator's allow-list is.
            severity="MEDIUM",
            confidence=70,
            device_id=device_id,
            first_observed_at=ordered[0].occurred_at,
            last_observed_at=ordered[-1].occurred_at,
            explanation=explanation,
            evidence=evidence,
            observations=tuple(links),
            is_simulation=context.is_simulation,
        )


def _scope_networks(
    settings: Settings, *, is_simulation: bool
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """The networks that count as "inside" for this scope."""
    if is_simulation:
        return [ipaddress.ip_network(block) for block in SIMULATION_NETWORKS]
    return list(settings.monitored_ip_networks)


def _within(address: str, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    """Containment test that fails closed on an unparseable address."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


__all__ = ["SIMULATION_NETWORKS", "UnexpectedCommunicationDetector"]
