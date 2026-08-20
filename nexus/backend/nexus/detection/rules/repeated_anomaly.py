"""Rule: repetition inside a window, counted two ways.

The problem this solves
-----------------------
A single anomalous observation is usually noise. One failed authentication, one
connection to an unusual port, one malformed packet — networks produce these
constantly, and alerting on each one produces an alert stream nobody reads. What
distinguishes signal from noise is *repetition within a bounded time*: five port
probes in fifteen minutes is a scan; five in a month is a coincidence.

Two conditions, because sensors see two kinds of evidence
---------------------------------------------------------
**Rate.** The same ``kind`` of anomalous observation, for one device, N times
inside a window. This works when the sensor has classified what it saw — a
syslog line saying "authentication failed" is self-describing, and forty of them
in ten minutes is a brute-force attempt.

**Enumeration.** One device contacting N *distinct endpoints* inside a window.
This is the only shape that works for flow data. A packet-capture sensor sees
`TRAFFIC` records; no individual flow is anomalous, and the anomaly lives
entirely in the spread. Counting raw flows instead would report that a busy
device is busy — which is why ``TRAFFIC`` is excluded from the rate condition's
default categories and handled here instead.

Both windows and both thresholds are configuration, because the right values
depend entirely on the network (``detection.config.rules.repeated_anomaly``).

Why the window is anchored to the observation, not to "now"
-----------------------------------------------------------
Anchoring to wall-clock time would make the rule's output depend on *when the
analysis job happened to run*. Re-analysing the same events an hour later would
count a different set and reach a different conclusion, so the pipeline would
stop being idempotent and a retried job could create a second finding for one
burst. Anchoring to the newest observation's own timestamp makes the count a
function of the data alone.

The fingerprint buckets the window start by the window length, so a burst
sustained across hours produces one finding per window rather than one per
observation — the finding accumulates evidence instead of multiplying.

This rule counts. It does not classify. A finding here says "this happened N
times in M seconds", which is a fact, and never says what caused it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from nexus.db.models.event import SecurityEvent
from nexus.detection.base import (
    Candidate,
    DetectionContext,
    Detector,
    max_severity,
    severity_at_least,
)
from nexus.detection.config import DetectionConfig
from nexus.detection.observation import Observation

# Evidence links per finding. A scan can produce thousands of observations; the
# finding cites a bounded sample and records the true count separately, so the
# link table cannot grow without bound.
MAX_EVIDENCE_LINKS = 20

CONFIDENCE_AT_THRESHOLD = 55
CONFIDENCE_PER_EXTRA = 5
CONFIDENCE_CEILING = 95


class RepeatedAnomalyDetector(Detector):
    id = "repeated_anomaly"
    detection_type = "REPEATED_ANOMALY"
    version = 1
    description = (
        "Repetition within a configured window: the same anomalous observation "
        "kind at a threshold rate, or one device contacting many distinct endpoints."
    )

    def _enabled(self, config: DetectionConfig) -> bool:
        return config.rules.repeated_anomaly.enabled

    async def evaluate(self, context: DetectionContext) -> list[Candidate]:
        candidates = await self._evaluate_rate(context)
        if context.config.rules.repeated_anomaly.enumeration_enabled:
            candidates.extend(await self._evaluate_enumeration(context))
        return candidates

    # ------------------------------------------------------------------ rate --

    async def _evaluate_rate(self, context: DetectionContext) -> list[Candidate]:
        rule = context.config.rules.repeated_anomaly

        interesting = [
            item
            for item in context.with_device()
            if item.category in rule.categories
            and severity_at_least(item.severity, rule.min_severity)
        ]
        if not interesting:
            return []

        # Group by (device, kind): "five different things happened" is not the
        # same claim as "one thing happened five times", and only the second is
        # what this rule reports.
        groups: dict[tuple[Any, str], list[Observation]] = {}
        for item in interesting:
            groups.setdefault((item.device_id, item.kind), []).append(item)

        window = timedelta(seconds=rule.window_seconds)
        candidates: list[Candidate] = []

        for (device_id, kind), observations in sorted(
            groups.items(), key=lambda entry: (str(entry[0][0]), entry[0][1])
        ):
            ordered = sorted(observations, key=lambda item: (item.occurred_at, item.event_id))
            newest = ordered[-1]
            window_end = newest.occurred_at
            window_start = window_end - window

            # Count against stored history, not just this batch: a burst
            # arriving in three separate ingestion batches is still one burst,
            # and a rule that only counted the current batch would miss it.
            total = (
                await context.session.execute(
                    select(func.count())
                    .select_from(SecurityEvent)
                    .where(
                        SecurityEvent.device_id == device_id,
                        SecurityEvent.kind == kind,
                        SecurityEvent.occurred_at >= window_start,
                        SecurityEvent.occurred_at <= window_end,
                        SecurityEvent.category.in_(rule.categories),
                        SecurityEvent.is_simulation.is_(context.is_simulation),
                    )
                )
            ).scalar_one()

            if total < rule.min_occurrences:
                continue

            candidates.append(
                self._build(
                    context,
                    device_id=device_id,
                    kind=kind,
                    observations=ordered,
                    total=int(total),
                    window_start=window_start,
                    window_end=window_end,
                )
            )

        return candidates

    # ----------------------------------------------------------- enumeration --

    async def _evaluate_enumeration(self, context: DetectionContext) -> list[Candidate]:
        """One device reaching many distinct endpoints in a short window.

        Counted from stored observations rather than from the batch, and counted
        as *distinct* ``(address, port)`` pairs rather than as flows. A device
        streaming video produces thousands of flows to two endpoints; a device
        enumerating a subnet produces few flows to hundreds. Only the second is
        interesting, and only the distinct count separates them.
        """
        rule = context.config.rules.repeated_anomaly

        with_endpoints = [
            item
            for item in context.with_device()
            if item.destination_ip and item.destination_port is not None
        ]
        if not with_endpoints:
            return []

        window = timedelta(seconds=rule.enumeration_window_seconds)
        by_device: dict[Any, list[Observation]] = {}
        for item in with_endpoints:
            by_device.setdefault(item.device_id, []).append(item)

        candidates: list[Candidate] = []
        for device_id, observations in sorted(by_device.items(), key=lambda entry: str(entry[0])):
            ordered = sorted(observations, key=lambda item: (item.occurred_at, item.event_id))
            window_end = ordered[-1].occurred_at
            window_start = window_end - window

            endpoints = (
                await context.session.execute(
                    select(SecurityEvent.destination_ip, SecurityEvent.destination_port)
                    .where(
                        SecurityEvent.device_id == device_id,
                        SecurityEvent.destination_ip.is_not(None),
                        SecurityEvent.destination_port.is_not(None),
                        SecurityEvent.occurred_at >= window_start,
                        SecurityEvent.occurred_at <= window_end,
                        SecurityEvent.is_simulation.is_(context.is_simulation),
                    )
                    .distinct()
                )
            ).all()

            if len(endpoints) < rule.min_distinct_endpoints:
                continue

            candidates.append(
                self._build_enumeration(
                    context,
                    device_id=device_id,
                    observations=ordered,
                    endpoints=[(str(row[0]), int(row[1])) for row in endpoints],
                    window_start=window_start,
                    window_end=window_end,
                )
            )
        return candidates

    def _build_enumeration(
        self,
        context: DetectionContext,
        *,
        device_id: Any,
        observations: list[Observation],
        endpoints: list[tuple[str, int]],
        window_start: Any,
        window_end: Any,
    ) -> Candidate:
        rule = context.config.rules.repeated_anomaly
        bucket = int(window_start.timestamp()) // rule.enumeration_window_seconds

        targets = sorted({address for address, _ in endpoints})
        ports = sorted({port for _, port in endpoints})

        confidence = min(
            CONFIDENCE_CEILING,
            CONFIDENCE_AT_THRESHOLD
            + CONFIDENCE_PER_EXTRA * (len(endpoints) - rule.min_distinct_endpoints),
        )

        sample = observations[:MAX_EVIDENCE_LINKS]
        sensors = sorted({item.provenance.sensor_name for item in observations})
        sensor_list = ", ".join("'" + name + "'" for name in sensors)

        evidence: dict[str, Any] = {
            "condition": "ENDPOINT_ENUMERATION",
            "distinct_endpoints": len(endpoints),
            "min_distinct_endpoints": rule.min_distinct_endpoints,
            "distinct_destinations": len(targets),
            "distinct_ports": len(ports),
            "window_seconds": rule.enumeration_window_seconds,
            "window_started_at": window_start.isoformat(),
            "window_ended_at": window_end.isoformat(),
            "destinations_sample": targets[:20],
            "ports_sample": ports[:50],
            "observed_by_sensors": sensors,
            "observations": [item.evidence_ref() for item in sample],
        }

        explanation = (
            f"This device contacted {len(endpoints)} distinct endpoints "
            f"({len(targets)} address(es) across {len(ports)} port(s)) between "
            f"{window_start.isoformat()} and {window_end.isoformat()} "
            f"({rule.enumeration_window_seconds} seconds), against a configured threshold of "
            f"{rule.min_distinct_endpoints}. Reported by {sensor_list}. This is the traffic "
            f"pattern produced by network scanning. It is also produced by legitimate "
            f"discovery, inventory and monitoring tools, so this finding reports the pattern "
            f"and not an intent."
        )

        links: list[tuple[int, str]] = [(sample[-1].event_id, "TRIGGER")]
        links.extend((item.event_id, "SUPPORTING") for item in sample[:-1])

        return Candidate(
            detector=self.id,
            rule_version=self.version,
            detection_type=self.detection_type,
            fingerprint_parts=("enumeration", "device", str(device_id), str(bucket)),
            # Enumeration from inside the network is worth a look on its own,
            # and capped here because an operator running a vulnerability scan
            # produces identical evidence.
            severity="MEDIUM",
            confidence=confidence,
            device_id=device_id,
            first_observed_at=min(item.occurred_at for item in sample),
            last_observed_at=max(item.occurred_at for item in sample),
            explanation=explanation,
            evidence=evidence,
            observations=tuple(links),
            is_simulation=context.is_simulation,
        )

    # ----------------------------------------------------------------- shared --

    def _build(
        self,
        context: DetectionContext,
        *,
        device_id: Any,
        kind: str,
        observations: list[Observation],
        total: int,
        window_start: Any,
        window_end: Any,
    ) -> Candidate:
        rule = context.config.rules.repeated_anomaly

        # Bucketed so a sustained burst maps onto one finding per window.
        # Integer division of an epoch second by the window length is stable
        # across processes and re-runs, unlike anything derived from "now".
        bucket = int(window_start.timestamp()) // rule.window_seconds

        confidence = min(
            CONFIDENCE_CEILING,
            CONFIDENCE_AT_THRESHOLD + CONFIDENCE_PER_EXTRA * (total - rule.min_occurrences),
        )

        # Capped at HIGH: repetition raises how much attention something
        # deserves, but "it happened a lot" is not by itself a critical
        # conclusion, and a rule that can reach CRITICAL on volume alone will.
        severity = max_severity([item.severity for item in observations], cap="HIGH")

        sensors = sorted({item.provenance.sensor_name for item in observations})
        sample = observations[:MAX_EVIDENCE_LINKS]

        evidence: dict[str, Any] = {
            "condition": "RATE",
            "kind": kind,
            "observed_occurrences": total,
            "min_occurrences": rule.min_occurrences,
            "window_seconds": rule.window_seconds,
            "window_started_at": window_start.isoformat(),
            "window_ended_at": window_end.isoformat(),
            "categories": list(rule.categories),
            "min_severity": rule.min_severity,
            "observed_by_sensors": sensors,
            "evidence_sample_size": len(sample),
            "observations": [item.evidence_ref() for item in sample],
        }

        # Built outside the f-string: quoting each name inside an f-string
        # expression needs a backslash escape, which is a syntax error before
        # Python 3.12, and this project targets 3.11.
        sensor_list = ", ".join("'" + name + "'" for name in sensors)

        explanation = (
            f"{total} observations of kind '{kind}' were recorded for this device between "
            f"{window_start.isoformat()} and {window_end.isoformat()} "
            f"({rule.window_seconds} seconds), against a configured threshold of "
            f"{rule.min_occurrences}. Reported by {sensor_list}. This finding reports the "
            f"rate of a repeated observation; it does not identify a cause."
        )

        links: list[tuple[int, str]] = [(sample[-1].event_id, "TRIGGER")]
        links.extend((item.event_id, "SUPPORTING") for item in sample[:-1])

        return Candidate(
            detector=self.id,
            rule_version=self.version,
            detection_type=self.detection_type,
            fingerprint_parts=("rate", "device", str(device_id), kind, str(bucket)),
            severity=severity,
            confidence=confidence,
            device_id=device_id,
            first_observed_at=observations[0].occurred_at,
            last_observed_at=observations[-1].occurred_at,
            explanation=explanation,
            evidence=evidence,
            observations=tuple(links),
            is_simulation=context.is_simulation,
        )


__all__ = ["RepeatedAnomalyDetector"]
