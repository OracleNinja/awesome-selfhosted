"""Deterministic, explainable risk scoring.

The model
---------
A device's risk score is a weighted sum of explicitly enumerated factors::

    score = clamp(round(sum of (weight_f * magnitude_f)), 0, 100)

where each ``magnitude`` is normalised to 0-1 by a documented saturation value
and each ``weight`` comes from the configuration document
(:mod:`nexus.detection.config`). Every factor is emitted with its code, label,
weight, magnitude, contribution and the evidence it was derived from — including
the factors that contributed nothing, because "this was considered and found
absent" is information an operator needs.

Why a weighted sum and not a model
----------------------------------
The specification for this milestone requires the scoring engine to contain no
machine learning and no language model, and that constraint is the right one for
a reason worth stating: a security score exists to be *argued with*. An operator
must be able to ask why a device is 72, get an answer in terms of the evidence,
change a weight, and see the number move predictably. A learned model gives a
number that is at best correlated with something and at worst unfalsifiable, and
on a console that drives real response actions that is a liability, not a
feature. Nothing here calls out to a model of any kind.

Idempotency, and why the clock is quantised
-------------------------------------------
Job delivery is at-least-once, so this must be safe to run repeatedly. Two
properties give that:

* **Recomputation, never accumulation.** The score is a pure function of the
  device's current active findings. There is no ``score += ...`` anywhere, so a
  replayed job cannot inflate anything. This is the reason the specification's
  requirement — "retrying must not repeatedly increase risk" — is satisfied
  structurally rather than by a guard.
* **Time enters only in whole hours.** The recency factor decays with the age of
  the newest finding, and age is floored to hours. Without that, two runs a
  second apart would produce microscopically different scores and append a new
  assessment row every time. With it, an unchanged input produces a byte-identical
  result and :func:`RiskService.rescore` writes nothing.

``UNKNOWN`` is not zero
-----------------------
A device that has never been assessed has ``risk_score = NULL`` and
``risk_level = 'UNKNOWN'``. A device assessed with no active findings has
``risk_score = 0`` and ``risk_level = 'LOW'``. These are different claims and the
schema, the API and the UI all keep them apart.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.db.models.device import Device
from nexus.db.models.finding import (
    ACTIVE_FINDING_STATUSES,
    DeviceRiskAssessment,
    Finding,
)
from nexus.detection.config import DetectionConfig

logger = get_logger(__name__)

# How much a finding of each severity is worth before confidence is applied.
# Published here rather than buried in a formula because it is part of the
# model's public description.
SEVERITY_VALUE: dict[str, float] = {
    "INFO": 0.0,
    "LOW": 0.25,
    "MEDIUM": 0.5,
    "HIGH": 0.8,
    "CRITICAL": 1.0,
}

# Time enters the model only in whole units of this size. See the module
# docstring: this is what makes repeated scoring runs no-ops.
DECAY_QUANTUM_SECONDS = 3600


@dataclass(frozen=True)
class RiskFactor:
    """One contributing factor, with everything needed to check the arithmetic."""

    code: str
    label: str
    weight: float
    magnitude: float
    contribution: float
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "weight": round(self.weight, 3),
            "magnitude": round(self.magnitude, 4),
            "contribution": round(self.contribution, 2),
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class RiskAssessment:
    """A computed score together with its full derivation."""

    score: int
    level: str
    factors: tuple[RiskFactor, ...]
    contributing_finding_ids: tuple[uuid.UUID, ...]
    window_started_at: datetime
    window_ended_at: datetime
    weights_fingerprint: str

    def factors_as_json(self) -> list[dict[str, Any]]:
        return [factor.as_dict() for factor in self.factors]


def level_for(score: int, config: DetectionConfig) -> str:
    """Map a score onto a band.

    ``UNKNOWN`` is deliberately unreachable from here: it means "not assessed",
    and this function is only ever called when an assessment has been made.
    """
    thresholds = config.thresholds
    if score >= thresholds.critical:
        return "CRITICAL"
    if score >= thresholds.high:
        return "HIGH"
    if score >= thresholds.medium:
        return "MEDIUM"
    return "LOW"


def score_finding(finding_severity: str, finding_confidence: int) -> int:
    """A single finding's own 0-100 contribution.

    Severity scaled by confidence, and nothing else. A HIGH finding the detector
    is only 35% sure of scores 28, which is the intended behaviour: the two axes
    stay visible right up to the point where a number is needed, and even then
    the number is trivially decomposable back into them.
    """
    value = SEVERITY_VALUE.get(finding_severity, 0.0)
    return max(0, min(100, round(value * finding_confidence)))


def compute_device_risk(
    device: Device,
    findings: list[Finding],
    *,
    config: DetectionConfig,
    now: datetime,
) -> RiskAssessment:
    """Score one device. Pure: no I/O, no clock reads, no randomness.

    ``findings`` must already be filtered to the device's *active* findings —
    resolved, suppressed and false-positive findings do not contribute, which is
    what makes triage meaningful rather than cosmetic.
    """
    weights = config.weights
    saturation = config.saturation
    factors: list[RiskFactor] = []

    active = sorted(findings, key=lambda item: item.last_observed_at)

    # ---- 1. Severity-weighted findings -------------------------------------
    raw = sum(SEVERITY_VALUE.get(item.severity, 0.0) * (item.confidence / 100.0) for item in active)
    magnitude = _ratio(raw, saturation.severity_weighted_findings)
    factors.append(
        RiskFactor(
            code="SEVERITY_WEIGHTED_FINDINGS",
            label="Open findings, weighted by severity and confidence",
            weight=weights.severity_weighted_findings,
            magnitude=magnitude,
            contribution=weights.severity_weighted_findings * magnitude,
            detail=(
                f"{len(active)} active finding(s) summing to {raw:.2f} severity-confidence "
                f"units against a saturation of {saturation.severity_weighted_findings}."
            ),
            evidence={
                "active_findings": len(active),
                "raw_units": round(raw, 4),
                "saturation": saturation.severity_weighted_findings,
                "by_severity": _count_by(active, lambda item: item.severity),
            },
        )
    )

    # ---- 2. Recency --------------------------------------------------------
    if active:
        newest = active[-1].last_observed_at
        age_seconds = _quantised_age(now, newest)
        magnitude = max(0.0, 1.0 - (age_seconds / saturation.recency_horizon_seconds))
        detail = (
            f"Most recent finding activity was {age_seconds // 3600} hour(s) ago; the factor "
            f"decays to zero at {saturation.recency_horizon_seconds // 3600} hours."
        )
        evidence = {
            "newest_finding_at": newest.isoformat(),
            "age_seconds_quantised": age_seconds,
            "horizon_seconds": saturation.recency_horizon_seconds,
        }
    else:
        magnitude = 0.0
        detail = "No active findings, so there is no recent activity to weigh."
        evidence = {"newest_finding_at": None}
    factors.append(
        RiskFactor(
            code="RECENT_ACTIVITY",
            label="Recency of the newest active finding",
            weight=weights.recent_activity,
            magnitude=magnitude,
            contribution=weights.recent_activity * magnitude,
            detail=detail,
            evidence=evidence,
        )
    )

    # ---- 3. Identity instability -------------------------------------------
    identity = [item for item in active if item.detection_type == "IDENTITY_CHANGE"]
    magnitude = _ratio(len(identity), saturation.identity_changes)
    factors.append(
        RiskFactor(
            code="IDENTITY_INSTABILITY",
            label="Changes to this device's MAC/IP association",
            weight=weights.identity_instability,
            magnitude=magnitude,
            contribution=weights.identity_instability * magnitude,
            detail=(
                f"{len(identity)} identity-change finding(s) against a saturation of "
                f"{saturation.identity_changes}."
            ),
            evidence={
                "identity_change_findings": len(identity),
                "saturation": saturation.identity_changes,
            },
        )
    )

    # ---- 4. External exposure ----------------------------------------------
    exposure = [item for item in active if item.detection_type == "UNEXPECTED_COMMUNICATION"]
    endpoints = sorted(
        {
            f"{item.evidence.get('destination_ip')}:{item.evidence.get('destination_port')}"
            for item in exposure
            if isinstance(item.evidence, dict)
        }
    )
    magnitude = _ratio(len(endpoints), saturation.external_endpoints)
    factors.append(
        RiskFactor(
            code="EXTERNAL_EXPOSURE",
            label="Distinct unexpected external endpoints contacted",
            weight=weights.external_exposure,
            magnitude=magnitude,
            contribution=weights.external_exposure * magnitude,
            detail=(
                f"{len(endpoints)} distinct unexpected endpoint(s) against a saturation of "
                f"{saturation.external_endpoints}."
            ),
            evidence={
                "endpoints": endpoints[:20],
                "endpoint_count": len(endpoints),
                "saturation": saturation.external_endpoints,
            },
        )
    )

    # ---- 5. Anomaly volume --------------------------------------------------
    anomalies = [item for item in active if item.detection_type == "REPEATED_ANOMALY"]
    magnitude = _ratio(len(anomalies), saturation.anomaly_findings)
    factors.append(
        RiskFactor(
            code="ANOMALY_VOLUME",
            label="Repeated anomalous observation windows",
            weight=weights.anomaly_volume,
            magnitude=magnitude,
            contribution=weights.anomaly_volume * magnitude,
            detail=(
                f"{len(anomalies)} repeated-anomaly finding(s) against a saturation of "
                f"{saturation.anomaly_findings}."
            ),
            evidence={
                "repeated_anomaly_findings": len(anomalies),
                "saturation": saturation.anomaly_findings,
            },
        )
    )

    # ---- 6. Novelty ---------------------------------------------------------
    novelty_seconds = saturation.novelty_hours * 3600
    device_age = _quantised_age(now, device.first_seen_at)
    is_novel = device_age < novelty_seconds
    magnitude = 1.0 if is_novel else 0.0
    factors.append(
        RiskFactor(
            code="DEVICE_NOVELTY",
            label="Device first observed recently",
            weight=weights.device_novelty,
            magnitude=magnitude,
            contribution=weights.device_novelty * magnitude,
            detail=(
                f"First observed {device_age // 3600} hour(s) ago; devices count as novel for "
                f"{saturation.novelty_hours} hours."
            ),
            evidence={
                "first_seen_at": device.first_seen_at.isoformat(),
                "novelty_hours": saturation.novelty_hours,
                "is_novel": is_novel,
            },
        )
    )

    # ---- 7. Operator trust (negative) ---------------------------------------
    magnitude = 1.0 if device.is_trusted else 0.0
    factors.append(
        RiskFactor(
            code="OPERATOR_TRUST",
            label="Operator has marked this device trusted",
            weight=weights.operator_trust,
            magnitude=magnitude,
            contribution=weights.operator_trust * magnitude,
            detail=(
                "Trust reduces the score but never suppresses detection: findings are still "
                "created, recorded and visible."
                if device.is_trusted
                else "Not marked trusted."
            ),
            evidence={"is_trusted": bool(device.is_trusted)},
        )
    )

    total = sum(factor.contribution for factor in factors)
    score = max(0, min(100, round(total)))

    if active:
        window_started_at = min(item.first_observed_at for item in active)
        window_ended_at = max(item.last_observed_at for item in active)
    else:
        window_started_at = now
        window_ended_at = now

    return RiskAssessment(
        score=score,
        level=level_for(score, config),
        factors=tuple(factors),
        contributing_finding_ids=tuple(item.id for item in active),
        window_started_at=window_started_at,
        window_ended_at=window_ended_at,
        weights_fingerprint=config.fingerprint(),
    )


class RiskService:
    """Persists risk assessments. Holds the caller's session, like every service here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_findings(self, device_id: uuid.UUID) -> list[Finding]:
        result = await self._session.execute(
            select(Finding).where(
                Finding.device_id == device_id,
                Finding.status.in_(ACTIVE_FINDING_STATUSES),
            )
        )
        return list(result.scalars().all())

    async def rescore(
        self,
        device: Device,
        *,
        config: DetectionConfig,
        now: datetime | None = None,
    ) -> tuple[RiskAssessment, bool]:
        """Recompute and persist one device's risk.

        Returns the assessment and whether anything changed. When nothing
        changed, no assessment row is written and the device row is untouched —
        which is what keeps a re-delivered job from filling the history table
        with identical entries.
        """
        moment = now or utcnow()

        # Lock the device row before reading its findings.
        #
        # Two writers reach here concurrently in normal operation: the analysis
        # pass rescores the devices it touched, and `risk.rescore_stale` walks
        # devices whose scores have aged (including those never scored at all).
        # Without this lock the two race, and the race is not benign: under READ
        # COMMITTED each transaction reads a snapshot, so the one that started
        # earlier sees *none* of the other's freshly committed findings,
        # computes a score from an empty finding set, and — committing last —
        # overwrites the correct score with a stale one. Observed in practice:
        # a device with an open finding was written back at the no-findings
        # score seconds after being scored correctly.
        #
        # FOR UPDATE serialises the two, and the statement that acquires it
        # takes a fresh snapshot, so the second writer sees the first's work
        # instead of racing it. Callers lock devices in id order (see the
        # analysis pass and the rescore job) so two multi-device passes cannot
        # deadlock by taking the same locks in opposite orders.
        locked = (
            await self._session.execute(
                select(Device)
                .where(Device.id == device.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if locked is None:
            # Deleted while we waited for the lock. Nothing to score, and
            # inventing an assessment for a device that no longer exists would
            # leave a row referencing nothing.
            return compute_device_risk(device, [], config=config, now=moment), False
        device = locked

        findings = await self.active_findings(device.id)
        assessment = compute_device_risk(device, findings, config=config, now=moment)

        unchanged = (
            device.risk_score == assessment.score
            and device.risk_level == assessment.level
            and device.risk_updated_at is not None
        )
        if unchanged:
            # The arithmetic can shift while the rounded score does not — a
            # factor's magnitude moving from 0.31 to 0.34 changes how the score
            # is explained without changing the score — so the derivation is
            # compared too, and a different derivation is recorded.
            #
            # Only the arithmetic, though. The factors also carry a human
            # `detail` string and an `evidence` blob, and those contain
            # timestamps that move every time a finding is re-observed. Comparing
            # the whole blob wrote a new assessment on every pass while a burst
            # was in progress: seven consecutive rows at score 47, seen in live
            # running. The history is meant to record movement, not ticking.
            previous = await self._latest_assessment(device.id)
            if previous is not None and _derivation(previous.factors) == _derivation(
                assessment.factors_as_json()
            ):
                return assessment, False

        previous_score = device.risk_score
        previous_level = device.risk_level if device.risk_score is not None else None

        self._session.add(
            DeviceRiskAssessment(
                device_id=device.id,
                assessed_at=moment,
                score=assessment.score,
                level=assessment.level,
                previous_score=previous_score,
                previous_level=previous_level,
                factors=assessment.factors_as_json(),
                contributing_finding_ids=[
                    str(item) for item in assessment.contributing_finding_ids
                ],
                window_started_at=assessment.window_started_at,
                window_ended_at=assessment.window_ended_at,
                weights_fingerprint=assessment.weights_fingerprint,
                is_simulation=device.is_simulation,
            )
        )

        device.risk_score = assessment.score
        device.risk_level = assessment.level
        device.risk_updated_at = moment

        logger.debug(
            "device_risk_updated",
            extra={
                "device_id": str(device.id),
                "score": assessment.score,
                "level": assessment.level,
                "previous_score": previous_score,
                "active_findings": len(findings),
            },
        )
        return assessment, True

    async def _latest_assessment(self, device_id: uuid.UUID) -> DeviceRiskAssessment | None:
        result = await self._session.execute(
            select(DeviceRiskAssessment)
            .where(DeviceRiskAssessment.device_id == device_id)
            .order_by(DeviceRiskAssessment.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


def _derivation(factors: Any) -> list[tuple[str, float, float, float]]:
    """The arithmetic of an assessment, without its explanatory text.

    Two assessments with the same derivation produce the same score for the same
    reasons, whatever their prose says.
    """
    if not isinstance(factors, list):  # pragma: no cover - defensive
        return []
    return [
        (
            str(factor.get("code")),
            float(factor.get("weight", 0.0)),
            float(factor.get("magnitude", 0.0)),
            float(factor.get("contribution", 0.0)),
        )
        for factor in factors
        if isinstance(factor, dict)
    ]


def _ratio(value: float, saturation: float) -> float:
    if saturation <= 0:  # pragma: no cover - configuration forbids this
        return 0.0
    return max(0.0, min(1.0, value / saturation))


def _quantised_age(now: datetime, moment: datetime) -> int:
    """Age in whole hours, expressed in seconds, never negative.

    Quantisation is what makes re-running the scorer a no-op; clamping at zero
    handles a sensor whose clock runs ahead of ours, which must not produce a
    magnitude above 1.
    """
    delta = (now - moment).total_seconds()
    if delta <= 0:
        return 0
    return int(delta // DECAY_QUANTUM_SECONDS) * DECAY_QUANTUM_SECONDS


def _count_by(items: list[Finding], key: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[key(item)] = counts.get(key(item), 0) + 1
    return counts


__all__ = [
    "DECAY_QUANTUM_SECONDS",
    "SEVERITY_VALUE",
    "RiskAssessment",
    "RiskFactor",
    "RiskService",
    "compute_device_risk",
    "level_for",
    "score_finding",
]
