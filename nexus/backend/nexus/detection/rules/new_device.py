"""Rule: a device NEXUS has no prior record of.

Why this is a security rule at all
----------------------------------
On a network you own, the arrival of a device you did not add is the single
cheapest signal available. It requires no traffic analysis, no signatures, and
no threat intelligence — only an inventory. Most home-network compromises that
are visible from the network layer are visible here first.

How "new" is decided
--------------------
Not by ``devices.first_seen_at`` — that column is maintained by ingestion and
moves under concurrent upserts. The rule instead asks the evidence directly:

    the lowest ``security_events.id`` that mentions this device

If that id falls inside the batch being analysed, this batch contains the
device's first observation. That definition is stable (event ids never change),
driver-agnostic (it does not depend on any sensor emitting a "discovered" event
kind), and re-derivable — the same question asked twice gives the same answer.

Confidence, honestly
--------------------
A device identified by a MAC address was seen on the local segment; the reading
is direct, so confidence is high. A device identified only by an IP address was
seen *through* something — a router, a syslog relay — and a recycled DHCP lease
produces exactly the same evidence. That is a genuinely weaker claim and the
confidence says so.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from nexus.db.models.device import Device
from nexus.db.models.event import SecurityEvent
from nexus.detection.base import Candidate, DetectionContext, Detector
from nexus.detection.config import DetectionConfig
from nexus.detection.observation import Observation

# How many supporting observations to attach. The trigger is what matters; a
# handful of neighbours give an investigator context without turning the
# evidence link table into a copy of the event stream.
MAX_SUPPORTING = 5


class NewDeviceDetector(Detector):
    id = "new_device"
    detection_type = "NEW_DEVICE"
    version = 1
    description = "A device with no prior observation in the event history."

    def _enabled(self, config: DetectionConfig) -> bool:
        return config.rules.new_device.enabled

    async def evaluate(self, context: DetectionContext) -> list[Candidate]:
        observations = context.with_device()
        if not observations:
            return []

        by_device: dict[uuid.UUID, list[Observation]] = {}
        for item in observations:
            if item.device_id is None:  # pragma: no cover - with_device() filters these
                continue
            by_device.setdefault(item.device_id, []).append(item)

        device_ids = list(by_device)

        # One grouped query for the whole batch rather than one per device: a
        # burst of discovery events can mention dozens of devices, and N+1
        # queries inside a job that runs every minute is how a background task
        # starts dominating database load.
        earliest = (
            await context.session.execute(
                select(SecurityEvent.device_id, func.min(SecurityEvent.id))
                .where(
                    SecurityEvent.device_id.in_(device_ids),
                    SecurityEvent.is_simulation.is_(context.is_simulation),
                )
                .group_by(SecurityEvent.device_id)
            )
        ).all()
        first_event_by_device = {row[0]: row[1] for row in earliest}

        new_device_ids = [
            device_id
            for device_id in device_ids
            if first_event_by_device.get(device_id, 0) >= context.first_event_id
        ]
        if not new_device_ids:
            return []

        devices = (
            (await context.session.execute(select(Device).where(Device.id.in_(new_device_ids))))
            .scalars()
            .all()
        )
        device_by_id = {device.id: device for device in devices}

        candidates: list[Candidate] = []
        for device_id in new_device_ids:
            device = device_by_id.get(device_id)
            if device is None:
                # Deleted between the analysis batch being read and now. There
                # is nothing to report about a device that no longer exists, and
                # inventing a placeholder would be fabrication.
                continue
            candidates.append(self._build(context, device, by_device[device_id]))
        return candidates

    def _build(
        self, context: DetectionContext, device: Device, observations: list[Observation]
    ) -> Candidate:
        # Ordered by event id, because "the device's first observation" is
        # defined by insertion order, not by the timestamp a sensor reported.
        ordered = sorted(observations, key=lambda item: item.event_id)
        trigger = ordered[0]
        cited = ordered[: 1 + MAX_SUPPORTING]

        identified_by_mac = bool(device.mac_address)
        confidence = 90 if identified_by_mac else 60

        identity = _describe(device)
        sensors = sorted({item.provenance.sensor_name for item in ordered})

        evidence: dict[str, Any] = {
            "device": {
                "id": str(device.id),
                "mac_address": device.mac_address,
                "ip_address": str(device.ip_address) if device.ip_address else None,
                "hostname": device.hostname,
                # Null means no OUI database is loaded, not "unknown vendor".
                # The finding repeats the distinction rather than flattening it.
                "vendor": device.vendor,
            },
            "identified_by": "mac_address" if identified_by_mac else "ip_address",
            "first_event_id": trigger.event_id,
            "observed_by_sensors": sensors,
            "observations_in_batch": len(ordered),
            "provenance": trigger.provenance.as_dict(),
        }

        explanation = (
            f"{identity} was observed for the first time at "
            f"{trigger.occurred_at.isoformat()} by sensor '{trigger.provenance.sensor_name}' "
            f"({trigger.provenance.driver}). No earlier observation of this device exists in "
            f"the retained event history."
        )
        if not identified_by_mac:
            explanation += (
                " It was identified by IP address only, so this may also be a previously "
                "seen device that received a different address."
            )

        links: list[tuple[int, str]] = [(trigger.event_id, "TRIGGER")]
        links.extend((item.event_id, "SUPPORTING") for item in cited[1:])

        return Candidate(
            detector=self.id,
            rule_version=self.version,
            detection_type=self.detection_type,
            fingerprint_parts=("device", str(device.id)),
            # A device appearing is worth a look, not an alarm: on any real
            # network it also happens every time a guest connects.
            severity="MEDIUM",
            confidence=confidence,
            device_id=device.id,
            # The window covers the observations this finding cites, computed
            # with min/max rather than by position: events arrive in id order,
            # not timestamp order — a batch containing a backdated flow would
            # otherwise produce a window that ends before it begins, which the
            # database rejects (and correctly so).
            first_observed_at=min(item.occurred_at for item in cited),
            last_observed_at=max(item.occurred_at for item in cited),
            explanation=explanation,
            evidence=evidence,
            observations=tuple(links),
            is_simulation=context.is_simulation,
        )


def _describe(device: Device) -> str:
    """A human-readable name for a device, preferring what a human wrote."""
    if device.label:
        return f"Device '{device.label}'"
    if device.hostname:
        return f"Device '{device.hostname}'"
    if device.mac_address:
        return f"Device with MAC address {device.mac_address}"
    return f"Device at {device.ip_address}"


__all__ = ["NewDeviceDetector"]
