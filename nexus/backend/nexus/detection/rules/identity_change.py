"""Rule: the association between a hardware address and an IP address changed.

What is actually being detected
-------------------------------
Ethernet delivers frames to a MAC address; IP routing addresses hosts by IP.
The mapping between the two is maintained by ARP, a protocol with no
authentication whatsoever: any host on the segment can answer "that address is
me". That is the mechanism behind ARP spoofing, and the observable consequence
is exactly what this rule looks for — a previously stable (MAC, IP) pair that
suddenly disagrees with itself.

Two directions, two meanings:

* ``IP_REASSIGNED`` — an address previously answered by one MAC is now answered
  by a different one. This is the shape of an address takeover.
* ``MAC_MOVED`` — a known device now appears at a different address. This is
  the shape of a DHCP lease change.

Why confidence is deliberately low
----------------------------------
Both patterns are produced constantly by entirely benign behaviour: DHCP leases
expire, phones rotate randomised MAC addresses by design, docks and adapters are
shared between laptops. A single observation cannot distinguish an attack from a
lease renewal, and claiming otherwise would train an operator to ignore the
alert.

So this rule is the clearest example of the two-axis model: severity is HIGH for
a takeover because *if* it is real it is serious, while confidence stays LOW
because a single observation is weak evidence. Confidence rises when the change
is abrupt — a mapping that was valid minutes ago has a much narrower set of
benign explanations than one that was valid last week.

Only observations carrying both a MAC and an IP are usable: the rule needs to
compare a *pair*, and half a pair is not evidence of anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from nexus.db.models.event import SecurityEvent
from nexus.detection.base import Candidate, DetectionContext, Detector
from nexus.detection.config import DetectionConfig
from nexus.detection.observation import Observation

# A change observed within this long of the previous reading is treated as
# abrupt. A DHCP lease is normally hours; minutes is not a renewal.
ABRUPT_SECONDS = 300

CONFIDENCE_BASE = 35
CONFIDENCE_ABRUPT = 60


class IdentityChangeDetector(Detector):
    id = "identity_change"
    detection_type = "IDENTITY_CHANGE"
    version = 1
    description = "A MAC/IP association that disagrees with the previous observation."

    def _enabled(self, config: DetectionConfig) -> bool:
        return config.rules.identity_change.enabled

    async def evaluate(self, context: DetectionContext) -> list[Candidate]:
        rule = context.config.rules.identity_change
        pairs = [
            item
            for item in context.observations
            if item.mac_address and item.source_ip and item.device_id is not None
        ]
        if not pairs:
            return []

        cutoff_at = context.now - timedelta(seconds=rule.lookback_seconds)

        macs = sorted({item.mac_address for item in pairs if item.mac_address})
        addresses = sorted({item.source_ip for item in pairs if item.source_ip})

        baseline_by_mac = await self._baseline(
            context, column=SecurityEvent.mac_address, values=macs, cutoff_at=cutoff_at
        )
        baseline_by_ip = await self._baseline(
            context, column=SecurityEvent.source_ip, values=addresses, cutoff_at=cutoff_at
        )

        candidates: list[Candidate] = []
        seen: set[tuple[str, ...]] = set()

        # Process in event order so that, when a batch contains several
        # observations of the same change, the earliest is the trigger and the
        # result does not depend on dictionary iteration order.
        for item in sorted(pairs, key=lambda obs: obs.event_id):
            mac = item.mac_address
            address = item.source_ip
            if mac is None or address is None:  # pragma: no cover - filtered above
                continue

            prior_for_address = baseline_by_ip.get(address)
            if prior_for_address is not None and prior_for_address[1] != mac:
                candidate = self._build(
                    context,
                    item,
                    change="IP_REASSIGNED",
                    previous_value=prior_for_address[1],
                    current_value=mac,
                    subject=address,
                    baseline_event_id=prior_for_address[0],
                    baseline_at=prior_for_address[2],
                )
                if candidate.fingerprint_parts not in seen:
                    seen.add(candidate.fingerprint_parts)
                    candidates.append(candidate)

            prior_for_mac = baseline_by_mac.get(mac)
            if prior_for_mac is not None and prior_for_mac[1] != address:
                candidate = self._build(
                    context,
                    item,
                    change="MAC_MOVED",
                    previous_value=prior_for_mac[1],
                    current_value=address,
                    subject=mac,
                    baseline_event_id=prior_for_mac[0],
                    baseline_at=prior_for_mac[2],
                )
                if candidate.fingerprint_parts not in seen:
                    seen.add(candidate.fingerprint_parts)
                    candidates.append(candidate)

        return candidates

    async def _baseline(
        self,
        context: DetectionContext,
        *,
        column: Any,
        values: list[str],
        cutoff_at: datetime,
    ) -> dict[str, tuple[int, str, datetime]]:
        """Most recent pre-batch observation per key.

        ``DISTINCT ON`` is PostgreSQL's answer to "the newest row per group": it
        keeps the first row of each group under the given ``ORDER BY``, which is
        one index-ordered pass instead of a window function over the whole set.

        The ``id < first_event_id`` predicate is what makes the rule idempotent.
        Without it, replaying a batch would compare an observation against
        itself — or against its own neighbour — and manufacture a change that
        the first pass correctly did not report.
        """
        if not values:
            return {}

        # The "other half" of the pair: keyed by MAC we want the address it was
        # seen at, and keyed by address we want the MAC that answered for it.
        if column is SecurityEvent.mac_address:
            other = SecurityEvent.source_ip
        else:
            other = SecurityEvent.mac_address

        rows = (
            await context.session.execute(
                select(column, other, SecurityEvent.id, SecurityEvent.occurred_at)
                .where(
                    column.in_(values),
                    other.is_not(None),
                    SecurityEvent.id < context.first_event_id,
                    SecurityEvent.occurred_at >= cutoff_at,
                    SecurityEvent.is_simulation.is_(context.is_simulation),
                )
                .distinct(column)
                .order_by(column, SecurityEvent.id.desc())
            )
        ).all()

        return {str(row[0]): (row[2], str(row[1]), row[3]) for row in rows}

    def _build(
        self,
        context: DetectionContext,
        observation: Observation,
        *,
        change: str,
        previous_value: str,
        current_value: str,
        subject: str,
        baseline_event_id: int,
        baseline_at: datetime,
    ) -> Candidate:
        gap_seconds = (observation.occurred_at - baseline_at).total_seconds()
        abrupt = 0 <= gap_seconds <= ABRUPT_SECONDS
        confidence = CONFIDENCE_ABRUPT if abrupt else CONFIDENCE_BASE

        if change == "IP_REASSIGNED":
            # An address answering for a different machine is the observable
            # signature of ARP spoofing — and also of a recycled DHCP lease.
            severity = "HIGH"
            headline = (
                f"IP address {subject} was previously associated with MAC address "
                f"{previous_value} and is now associated with {current_value}."
            )
            benign = (
                "The same evidence is produced by a DHCP lease being reassigned to a "
                "different device."
            )
        else:
            severity = "MEDIUM"
            headline = (
                f"MAC address {subject} was previously observed at IP address "
                f"{previous_value} and is now observed at {current_value}."
            )
            benign = (
                "The same evidence is produced by a normal DHCP lease renewal onto a "
                "different address."
            )

        explanation = (
            f"{headline} The previous association was recorded by event {baseline_event_id} at "
            f"{baseline_at.isoformat()}; the new one by event {observation.event_id} at "
            f"{observation.occurred_at.isoformat()} from sensor "
            f"'{observation.provenance.sensor_name}'. The two observations are "
            f"{int(gap_seconds)} seconds apart. {benign} This finding reports the change "
            f"itself, not an attribution."
        )

        evidence: dict[str, Any] = {
            "change": change,
            "subject": subject,
            "previous_value": previous_value,
            "current_value": current_value,
            "baseline_event_id": baseline_event_id,
            "baseline_observed_at": baseline_at.isoformat(),
            "current_event_id": observation.event_id,
            "current_observed_at": observation.occurred_at.isoformat(),
            "gap_seconds": int(gap_seconds),
            "abrupt": abrupt,
            "lookback_seconds": context.config.rules.identity_change.lookback_seconds,
            "provenance": observation.provenance.as_dict(),
        }

        return Candidate(
            detector=self.id,
            rule_version=self.version,
            detection_type=self.detection_type,
            fingerprint_parts=(change, subject, previous_value, current_value),
            severity=severity,
            confidence=confidence,
            device_id=observation.device_id,
            first_observed_at=observation.occurred_at,
            last_observed_at=observation.occurred_at,
            explanation=explanation,
            evidence=evidence,
            observations=(
                (observation.event_id, "TRIGGER"),
                (baseline_event_id, "BASELINE"),
            ),
            is_simulation=context.is_simulation,
        )


__all__ = ["IdentityChangeDetector"]
