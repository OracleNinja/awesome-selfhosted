"""The detector contract.

A detector is a pure-ish function from *observations plus stored history* to
*candidate findings*. It does not write anything. Persistence, deduplication,
risk scoring and auditing all happen in one place afterwards
(:mod:`nexus.detection.engine`), which is what keeps those guarantees uniform
instead of re-implemented, slightly differently, in every rule.

Determinism is the contract
---------------------------
Given the same observations and the same stored history, a detector must
produce the same candidates — same fingerprints, same severity, same confidence.
That is what makes the pipeline safe to re-run, which it will be: job delivery
is at-least-once (:mod:`nexus.services.jobs`). Two consequences for anyone
writing a rule:

* Never use ``random``, and never use "now" for anything that ends up in a
  fingerprint. Bucket on the observation's own timestamp instead.
* Never mutate an :class:`~nexus.detection.observation.Observation`. They are
  frozen, and detectors run in sequence over the same batch.

Severity and confidence are different questions
-----------------------------------------------
Every candidate answers both, separately:

* **Severity** — how bad this is *if the conclusion is right*.
* **Confidence** — how likely it is that the conclusion is right.

A rule that sees one ARP reply claiming a known address now belongs to a
different MAC produces ``severity=HIGH, confidence=LOW``: address takeover is
serious, and a DHCP reassignment looks identical from one observation. Folding
those into a single "score" at detection time throws away the distinction an
analyst uses to decide what to look at first, so the model keeps them apart all
the way to the API.

Scope isolation
---------------
The engine runs detectors once per *scope* — real observations and simulated
observations are separate passes with separate history queries. A detector
therefore cannot accidentally correlate a laboratory event with a real one; the
isolation is structural rather than a rule each detector has to remember.
"""

from __future__ import annotations

import hashlib
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.detection.config import DetectionConfig
from nexus.detection.observation import Observation

# Ordering for "at least this severe" comparisons. Kept here rather than on the
# event model because it is a detection concern: the storage layer only needs
# the values to be valid, not ordered.
SEVERITY_ORDER: dict[str, int] = {
    "INFO": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


def severity_at_least(value: str, minimum: str) -> bool:
    return SEVERITY_ORDER.get(value, -1) >= SEVERITY_ORDER.get(minimum, 99)


def max_severity(values: list[str], *, cap: str | None = None) -> str:
    """Highest severity present, optionally capped.

    The cap matters: a rule that aggregates CRITICAL observations should not
    automatically produce a CRITICAL finding, because "this happened five times"
    is a different claim from "this is critical".
    """
    if not values:
        return "INFO"
    highest = max(values, key=lambda item: SEVERITY_ORDER.get(item, -1))
    if cap is not None and SEVERITY_ORDER.get(highest, 0) > SEVERITY_ORDER.get(cap, 4):
        return cap
    return highest


AvailabilityState = Literal["ACTIVE", "DISABLED", "NOT_CONFIGURED"]


@dataclass(frozen=True)
class DetectorAvailability:
    """Whether a detector can actually run, and why not if it cannot.

    ``NOT_CONFIGURED`` is a first-class answer, not an error. A rule that needs
    to know which networks belong to the operator cannot function until the
    operator says so, and reporting that plainly is the difference between "no
    findings because everything is fine" and "no findings because this rule has
    never run". The status API surfaces both, with the remedy.
    """

    state: AvailabilityState
    detail: str | None = None
    remedy: str | None = None

    @property
    def can_run(self) -> bool:
        return self.state == "ACTIVE"


@dataclass(frozen=True)
class DetectionContext:
    """One scope's worth of work.

    ``first_event_id``/``last_event_id`` bound the batch. Detectors use them to
    separate "observations I am being asked about" from "history I may compare
    against", which is what stops a rule re-firing on its own trigger event the
    next time the window is replayed.
    """

    session: AsyncSession
    settings: Settings
    config: DetectionConfig
    observations: tuple[Observation, ...]
    is_simulation: bool
    first_event_id: int
    last_event_id: int
    now: datetime

    def with_device(self) -> tuple[Observation, ...]:
        """Observations that resolved to a device.

        Every rule here reasons about devices, and an observation with no device
        (a syslog line from an unresolvable source, say) is not evidence about
        one. Dropping it is honest; attributing it to a guess would not be.
        """
        return tuple(item for item in self.observations if item.device_id is not None)


@dataclass(frozen=True)
class Candidate:
    """A proposed finding, before it touches the database.

    ``fingerprint_parts`` is the *identity* of the thing detected — not of this
    particular firing. Two passes over overlapping windows that observe the same
    address takeover produce the same parts, therefore the same fingerprint,
    therefore one row. See :mod:`nexus.db.models.finding`.
    """

    detector: str
    rule_version: int
    detection_type: str
    fingerprint_parts: tuple[str, ...]
    severity: str
    confidence: int
    device_id: uuid.UUID | None
    first_observed_at: datetime
    last_observed_at: datetime
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)
    # (event_id, role) pairs. The engine inserts them into the evidence link
    # table, whose composite primary key makes a repeated link a no-op.
    observations: tuple[tuple[int, str], ...] = ()
    is_simulation: bool = False

    def fingerprint(self) -> str:
        """Stable 64-character identity for this finding.

        Hashed rather than stored as a readable composite key for two reasons:
        the parts include addresses and identifiers of unbounded length, and a
        fixed-width column keeps the unique index small. The parts themselves
        are preserved in ``evidence`` so the identity remains explainable.
        """
        parts = (
            self.detector,
            str(self.rule_version),
            "sim" if self.is_simulation else "real",
            *self.fingerprint_parts,
        )
        joined = "\x1f".join(parts)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class Detector(ABC):
    """One deterministic rule.

    Subclasses declare their identity as class attributes so the registry, the
    status API and stored findings all name the rule the same way. ``version``
    is part of a finding's record: retuning a rule changes its conclusions, and
    an investigator needs to know which logic applied.
    """

    id: str
    detection_type: str
    version: int = 1
    description: str = ""

    def availability(
        self, config: DetectionConfig, settings: Settings, *, is_simulation: bool
    ) -> DetectorAvailability:
        """Whether this rule can run in this scope.

        The default is "enabled means active"; rules with an external
        prerequisite override it. Scope matters because a rule can be runnable
        for laboratory data and not for the real network, or the reverse.
        """
        if not self._enabled(config):
            return DetectorAvailability(
                state="DISABLED",
                detail="Disabled in the detection configuration.",
                remedy="Enable it under detection.config.rules.",
            )
        return DetectorAvailability(state="ACTIVE")

    @abstractmethod
    def _enabled(self, config: DetectionConfig) -> bool:
        """Read this rule's enable flag out of the configuration document."""

    @abstractmethod
    async def evaluate(self, context: DetectionContext) -> list[Candidate]:
        """Examine the batch and propose findings. Must not write."""


__all__ = [
    "SEVERITY_ORDER",
    "Candidate",
    "DetectionContext",
    "Detector",
    "DetectorAvailability",
    "max_severity",
    "severity_at_least",
]
