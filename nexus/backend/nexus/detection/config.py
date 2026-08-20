"""Detection rule parameters and risk weights.

Two kinds of configuration exist in NEXUS and they live in different places for
a reason (see :mod:`nexus.db.models.setting`). Detection tuning is the second
kind: an operator changes a threshold at 02:00 during an incident and needs it
to take effect without a redeploy. So it lives in ``app_settings`` under one
key, ``detection.config``, validated on write and on read.

Why one row rather than one row per knob
----------------------------------------
A risk model is only coherent as a whole — weights are relative to each other,
and a half-applied change produces scores that mean nothing. One JSONB document
with the existing optimistic-concurrency ``version`` column makes an edit atomic
and makes two administrators editing from two tabs a ``409 CONFLICT`` rather
than a silent overwrite.

Why validation happens twice
----------------------------
On write, so a malformed document cannot be stored. On read, because the row is
data in a database that a future migration, a restore from backup, or someone
with ``psql`` can change. If the stored document is invalid, this module falls
back to the documented defaults and **says so** — the status endpoint reports
``DEFAULT_AFTER_INVALID_CONFIGURATION`` with the validation error. Silently
using defaults would leave an operator convinced their tuning was in force.

No machine learning, deliberately
---------------------------------
The scoring model here is a weighted sum of explicitly enumerated factors. That
is a design decision, not a placeholder: every score must be reproducible from
stored inputs and explainable to a person, and a model whose weights are learned
is neither. The structure — named factors, published weights, a fingerprint of
the weight table stored with each score — is what makes "why is this device 72?"
answerable years later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.logging import get_logger
from nexus.db.models.setting import AppSetting

logger = get_logger(__name__)

SETTING_KEY = "detection.config"

# Bumped when the *meaning* of a stored document changes in a way that older
# documents cannot express. Stored alongside the value so a loader can tell a
# valid old document from an invalid new one.
CONFIG_SCHEMA_VERSION = 1


class RiskWeights(BaseModel):
    """How much each contributing factor can add to a device's risk score.

    Every weight is expressed in score points: a factor at full magnitude
    contributes exactly its weight. The positive weights sum to more than 100 on
    purpose — a device that is bad in every dimension saturates at 100 rather
    than requiring each factor to be scaled down — and the total is clamped.

    ``operator_trust`` is negative: an operator vouching for a device reduces
    its score. It never suppresses *detection*; findings are still created,
    recorded and visible. See ``Device.is_trusted``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity_weighted_findings: float = Field(default=45.0, ge=0, le=100)
    recent_activity: float = Field(default=15.0, ge=0, le=100)
    identity_instability: float = Field(default=15.0, ge=0, le=100)
    external_exposure: float = Field(default=15.0, ge=0, le=100)
    anomaly_volume: float = Field(default=10.0, ge=0, le=100)
    device_novelty: float = Field(default=10.0, ge=0, le=100)
    operator_trust: float = Field(default=-30.0, ge=-100, le=0)


class RiskSaturation(BaseModel):
    """The point at which "more of this" stops raising the score.

    Without saturation, a device with 400 findings would score the same as one
    with 40 — both clamped at 100 — and the score would carry no information in
    its top half. Each factor is normalised to 0-1 by dividing by its saturation
    value, so the weights stay comparable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Sum of (severity value x confidence) across active findings at which this
    # factor reaches full magnitude. 2.0 is roughly "two confident HIGHs".
    severity_weighted_findings: float = Field(default=2.0, gt=0, le=100)
    identity_changes: int = Field(default=2, ge=1, le=1000)
    external_endpoints: int = Field(default=3, ge=1, le=1000)
    anomaly_findings: int = Field(default=2, ge=1, le=1000)
    # How long after a finding the "recent activity" factor decays to zero.
    recency_horizon_seconds: int = Field(default=72 * 3600, ge=300, le=90 * 86400)
    # A device first seen more recently than this counts as novel.
    novelty_hours: int = Field(default=24, ge=1, le=8760)


class RiskThresholds(BaseModel):
    """Score bands. ``UNKNOWN`` is not a band — it means "not yet assessed"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    medium: int = Field(default=35, ge=1, le=100)
    high: int = Field(default=60, ge=1, le=100)
    critical: int = Field(default=80, ge=1, le=100)


class NewDeviceRule(BaseModel):
    """A device NEXUS has no prior record of.

    No threshold to tune: "we have never seen this before" is a fact about the
    stored evidence, not a judgement call. Only enablement is configurable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True


class IdentityChangeRule(BaseModel):
    """The MAC/IP association for a device changed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    # How far back to look for the baseline association. Beyond this, an old
    # address is treated as forgotten rather than as evidence of a change —
    # otherwise a device returning from a six-month absence looks like an
    # attack.
    lookback_seconds: int = Field(default=7 * 86400, ge=300, le=365 * 86400)


class RepeatedAnomalyRule(BaseModel):
    """Repetition inside a window, counted two ways.

    A single anomalous observation is noise; repetition within a bounded time is
    signal. But "repetition" means two different things depending on what the
    sensors can see, and a rule that only counts one of them has a blind spot:

    * **Rate** — the same *kind* of anomalous observation, N times in a window.
      This is the right shape for authentication failures and for any sensor
      that classifies what it saw.
    * **Enumeration** — one device contacting N *distinct endpoints* in a
      window. This is the right shape for flow data, where every observation is
      an ordinary `TRAFFIC` record and the anomaly is in the spread, not in any
      individual flow. Counting raw flows here instead would fire on every busy
      device on the network.

    Both counts come from stored observations and both thresholds are
    configuration.

    A note on ``categories``: the default deliberately excludes ``TRAFFIC`` and
    ``LOG``. Those are the highest-volume categories, and a rate rule over them
    reports that a busy device is busy. Flow-shaped evidence is handled by the
    enumeration condition instead.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True

    # --- rate condition ---
    window_seconds: int = Field(default=900, ge=60, le=86400)
    min_occurrences: int = Field(default=5, ge=2, le=10_000)
    categories: tuple[str, ...] = ("ANOMALY", "SCAN", "AUTH")
    min_severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"

    # --- enumeration condition ---
    enumeration_enabled: bool = True
    enumeration_window_seconds: int = Field(default=300, ge=60, le=86400)
    # Distinct (address, port) pairs one device contacted. 15 is well above what
    # ordinary browsing or an update check produces and well below a scan;
    # operators on unusual networks will want to tune it, which is why it is
    # here rather than in the code.
    min_distinct_endpoints: int = Field(default=15, ge=2, le=100_000)


class UnexpectedCommunicationRule(BaseModel):
    """A monitored device talking to an endpoint outside the expected pattern.

    "Unexpected" is defined precisely and narrowly: the destination is outside
    every network the operator declared as theirs, and the destination port is
    not on the allowed list. That is a description of the observation, not an
    accusation — the finding says "unexpected", never "malicious", because
    nothing here can distinguish a new streaming service from a beacon.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    # Ports that ordinary devices legitimately reach on the internet.
    allowed_destination_ports: tuple[int, ...] = (53, 80, 123, 443, 853, 993, 995, 8443)
    # Traffic between two addresses inside the monitored networks is internal
    # and not what this rule is about.
    ignore_internal_destinations: bool = True


class RuleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    new_device: NewDeviceRule = NewDeviceRule()
    identity_change: IdentityChangeRule = IdentityChangeRule()
    repeated_anomaly: RepeatedAnomalyRule = RepeatedAnomalyRule()
    unexpected_communication: UnexpectedCommunicationRule = UnexpectedCommunicationRule()


class DetectionConfig(BaseModel):
    """Everything that tunes detection and scoring, as one validated document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = CONFIG_SCHEMA_VERSION
    weights: RiskWeights = RiskWeights()
    saturation: RiskSaturation = RiskSaturation()
    thresholds: RiskThresholds = RiskThresholds()
    rules: RuleConfig = RuleConfig()

    def fingerprint(self) -> str:
        """Digest of this configuration.

        Stored with every risk assessment. Two scores computed under different
        weights are not comparable, and without recording which weights applied
        there is no way to tell them apart after the fact.
        """
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Source of the configuration actually in force, reported by the status API.
ConfigSource = Literal[
    "DEFAULT",  # nothing stored; built-in defaults
    "CONFIGURED",  # a valid stored document
    "DEFAULT_AFTER_INVALID_CONFIGURATION",  # stored document rejected; see detail
]


@dataclass(frozen=True)
class LoadedConfig:
    """A configuration plus an honest account of where it came from."""

    config: DetectionConfig
    source: ConfigSource
    version: int | None = None
    detail: str | None = None

    @property
    def is_default(self) -> bool:
        return self.source != "CONFIGURED"


DEFAULT_CONFIG = DetectionConfig()


async def load_config(session: AsyncSession) -> LoadedConfig:
    """Read and validate the stored configuration, or fall back loudly.

    Never raises on bad stored data: a corrupt settings row must not stop
    detection from running at all. It must, however, be impossible to mistake
    the fallback for the operator's intent, which is what ``source`` and
    ``detail`` are for.
    """
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == SETTING_KEY))
    ).scalar_one_or_none()

    if row is None:
        return LoadedConfig(config=DEFAULT_CONFIG, source="DEFAULT")

    try:
        config = DetectionConfig.model_validate(row.value)
    except ValidationError as exc:
        detail = _first_error(exc)
        logger.error(
            "detection_config_invalid",
            extra={"setting_key": SETTING_KEY, "detail": detail},
        )
        return LoadedConfig(
            config=DEFAULT_CONFIG,
            source="DEFAULT_AFTER_INVALID_CONFIGURATION",
            version=row.version,
            detail=detail,
        )

    return LoadedConfig(config=config, source="CONFIGURED", version=row.version)


def _first_error(exc: ValidationError) -> str:
    """One readable line naming the offending field.

    Only the location and the message — never the submitted value, which is the
    same rule the API's validation handler follows.
    """
    errors = exc.errors()
    if not errors:  # pragma: no cover - pydantic always populates this
        return "The stored detection configuration is not valid."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "<document>"
    return f"{location}: {first.get('msg', 'invalid')}"


def merge_config(current: DetectionConfig, patch: dict[str, Any]) -> DetectionConfig:
    """Apply a partial update, validating the result as a whole.

    Deep-merged one level into each section so an operator can adjust a single
    weight without restating the entire document — and validated afterwards, so
    a partial update cannot produce a document that would be rejected on load.
    """
    base = current.model_dump(mode="json")
    for section, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(section), dict):
            merged_section = dict(base[section])
            for key, inner in value.items():
                if isinstance(inner, dict) and isinstance(merged_section.get(key), dict):
                    merged_section[key] = {**merged_section[key], **inner}
                else:
                    merged_section[key] = inner
            base[section] = merged_section
        else:
            base[section] = value
    base["schema_version"] = CONFIG_SCHEMA_VERSION
    return DetectionConfig.model_validate(base)


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "DEFAULT_CONFIG",
    "SETTING_KEY",
    "ConfigSource",
    "DetectionConfig",
    "LoadedConfig",
    "RiskSaturation",
    "RiskThresholds",
    "RiskWeights",
    "RuleConfig",
    "load_config",
    "merge_config",
]
