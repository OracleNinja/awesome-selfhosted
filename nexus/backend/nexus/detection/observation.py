"""The canonical observation model.

What problem this solves
------------------------
Detection rules must reason about *observations*, and an observation is not the
same thing as a database row. A rule that reads ``SecurityEvent`` directly is
coupled to the ORM, to lazy loading, to whichever columns happen to exist, and
— worst — it can quietly forget to check provenance. Every rule would then have
to remember, on its own, that a laboratory event must never be correlated with a
real one.

So M5 defines one immutable value type that every detector consumes. It carries
the normalised fields a rule needs plus a :class:`Provenance` record that
answers, for any observation, the three questions the specification demands:

    Which sensor produced this observation?   -> provenance.sensor_name / sensor_id
    When was it produced?                     -> provenance.occurred_at / received_at
    Which raw event supports it?              -> provenance.event_id / event_uid / has_raw

Why a projection rather than a second table
-------------------------------------------
``security_events`` (M4) already stores every one of those fields. Copying them
into a parallel "observations" table would create two records of the same fact
that can disagree, double the storage of the highest-volume table in the system,
and require a synchronisation job whose failure mode is silent divergence.

The canonical model is therefore a **read-side projection**: one table, one
typed contract, built at the boundary in :func:`from_event`. That is the same
pattern the API schemas use — pydantic models projecting ORM rows — applied to
the detection layer.

Immutability
------------
``frozen=True`` is not decoration. Detectors run in sequence over the same batch;
if one could mutate an observation, the next detector's conclusion would depend
on execution order, and the pipeline would stop being deterministic. A frozen
dataclass makes that a ``FrozenInstanceError`` at the moment of the mistake
rather than a wrong finding six weeks later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nexus.db.models.event import SecurityEvent


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where an observation came from, and what backs it up.

    Every field here is a *fact about the recording*, never an inference. If a
    detector ever needs to explain itself — and in this system it always does —
    this is what it cites.
    """

    event_id: int
    event_uid: uuid.UUID
    sensor_id: uuid.UUID | None
    sensor_name: str
    driver: str
    occurred_at: datetime
    received_at: datetime
    # Taken from the driver class at ingestion, never from the observation
    # payload. A laboratory sensor cannot produce data that claims to be real.
    is_simulation: bool
    # Whether the original, pre-normalisation form was retained. False is an
    # honest "we no longer hold the raw evidence", not "there was none".
    has_raw: bool

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe rendering, for storage in a finding's evidence blob."""
        return {
            "event_id": self.event_id,
            "event_uid": str(self.event_uid),
            "sensor_id": str(self.sensor_id) if self.sensor_id else None,
            "sensor_name": self.sensor_name,
            "driver": self.driver,
            "occurred_at": self.occurred_at.isoformat(),
            "received_at": self.received_at.isoformat(),
            "is_simulation": self.is_simulation,
            "raw_retained": self.has_raw,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    """One normalised, provenance-carrying observation.

    The network fields are exactly those of ``security_events``; nothing is
    derived, defaulted, or guessed here. A field that was not observed is
    ``None`` — which is a different statement from zero, and detectors are
    written to respect the difference.
    """

    provenance: Provenance

    category: str
    kind: str
    severity: str
    # The *sensor's* confidence in its own reading, 0-100. Distinct from a
    # finding's confidence, which is the detector's confidence in a conclusion
    # drawn from possibly several of these.
    confidence: int

    device_id: uuid.UUID | None
    source_ip: str | None
    destination_ip: str | None
    source_port: int | None
    destination_port: int | None
    protocol: str | None
    mac_address: str | None

    summary: str
    attributes: dict[str, Any]

    # ------------------------------------------------------------------ views --

    @property
    def event_id(self) -> int:
        return self.provenance.event_id

    @property
    def occurred_at(self) -> datetime:
        return self.provenance.occurred_at

    @property
    def is_simulation(self) -> bool:
        return self.provenance.is_simulation

    def evidence_ref(self) -> dict[str, Any]:
        """A bounded snapshot of this observation, for a finding's evidence.

        Findings outlive events: retention prunes the event stream, and the
        ``ON DELETE CASCADE`` on the evidence link takes the link with it. This
        snapshot is what keeps a finding explainable afterwards. It is
        deliberately small — the finding is not a second copy of the event
        table, it is a citation.
        """
        return {
            "event_id": self.provenance.event_id,
            "occurred_at": self.provenance.occurred_at.isoformat(),
            "sensor_name": self.provenance.sensor_name,
            "driver": self.provenance.driver,
            "kind": self.kind,
            "severity": self.severity,
            "summary": self.summary[:200],
            "source_ip": self.source_ip,
            "destination_ip": self.destination_ip,
            "destination_port": self.destination_port,
            "protocol": self.protocol,
            "mac_address": self.mac_address,
            "is_simulation": self.provenance.is_simulation,
        }


def from_event(event: SecurityEvent) -> Observation:
    """Project one stored event into the canonical model.

    Addresses are stringified here because asyncpg returns ``IPv4Address`` /
    ``IPv6Address`` objects for ``INET`` columns. Detectors compare, group and
    serialise addresses; doing the conversion once at the boundary means no rule
    has to remember, and no evidence blob ends up holding a Python repr.
    """
    return Observation(
        provenance=Provenance(
            event_id=event.id,
            event_uid=event.event_uid,
            sensor_id=event.sensor_id,
            sensor_name=event.sensor_name,
            driver=event.driver,
            occurred_at=event.occurred_at,
            received_at=event.received_at,
            is_simulation=event.is_simulation,
            has_raw=event.raw is not None,
        ),
        category=event.category,
        kind=event.kind,
        severity=event.severity,
        confidence=event.confidence,
        device_id=event.device_id,
        source_ip=_address(event.source_ip),
        destination_ip=_address(event.destination_ip),
        source_port=event.source_port,
        destination_port=event.destination_port,
        protocol=event.protocol,
        mac_address=event.mac_address,
        summary=event.summary,
        attributes=dict(event.attributes or {}),
    )


def _address(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = ["Observation", "Provenance", "from_event"]
