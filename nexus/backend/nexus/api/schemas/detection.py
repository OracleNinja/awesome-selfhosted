"""Schemas for findings, evidence, risk, and detection status."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexus.api.schemas.common import IPAddressStr

SEVERITY_DESCRIPTION = (
    "How serious this finding would be if the conclusion is correct. Independent "
    "of `confidence`: a HIGH severity finding with LOW confidence is normal and "
    "means 'this would matter, and we are not sure'."
)

CONFIDENCE_DESCRIPTION = (
    "0-100. How likely the detector considers its own conclusion to be correct, "
    "based on the evidence it had. Never combined with severity into a single "
    "number by the server."
)


class FindingSummary(BaseModel):
    """One finding as it appears in a list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    detection_type: str = Field(
        description="NEW_DEVICE | IDENTITY_CHANGE | REPEATED_ANOMALY | UNEXPECTED_COMMUNICATION"
    )
    detector: str = Field(description="The rule that produced this finding.")
    rule_version: int = Field(
        description="Version of the rule that applied. Retuned rules reach different conclusions."
    )
    severity: str = Field(description=SEVERITY_DESCRIPTION)
    confidence: int = Field(description=CONFIDENCE_DESCRIPTION)
    risk_score: int | None = Field(
        default=None,
        description="This finding's own 0-100 contribution: severity scaled by confidence.",
    )
    status: str = Field(description="OPEN | ACKNOWLEDGED | RESOLVED | SUPPRESSED | FALSE_POSITIVE")
    device_id: uuid.UUID | None = None
    first_observed_at: datetime
    last_observed_at: datetime
    observation_count: int = Field(
        description="Observations linked to this finding since it was created."
    )
    explanation: str = Field(
        description="Plain-English account of what was observed and what was compared."
    )
    is_simulation: bool = Field(
        description=(
            "True for findings derived from laboratory data. Excluded from "
            "real-network views by default; never presented as an observation of "
            "your network."
        )
    )
    created_at: datetime
    updated_at: datetime


class FindingDetail(FindingSummary):
    """One finding with its structured evidence and derivation."""

    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The values the rule compared and the thresholds it applied, plus a "
            "bounded snapshot of the observations. Retained even after the "
            "underlying events are pruned by retention."
        ),
    )
    risk_factors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The factors behind this finding's own risk score.",
    )
    triaged_at: datetime | None = None
    triage_note: str | None = None
    triaged_by_user_id: uuid.UUID | None = None
    evidence_state: str = Field(
        description=(
            "COMPLETE — every linked observation is still retained.\n"
            "PARTIAL — some have been removed by event retention.\n"
            "PRUNED — none remain; the finding stands on its stored evidence snapshot."
        )
    )
    observations_available: int = Field(
        description="Linked observations that still exist in the event store."
    )


class FindingPage(BaseModel):
    items: list[FindingSummary]
    total: int
    has_more: bool


class EvidenceObservation(BaseModel):
    """One observation cited by a finding, with its provenance."""

    model_config = ConfigDict(from_attributes=True)

    event_id: int
    role: str = Field(
        description=(
            "TRIGGER — the observation that made the rule fire.\n"
            "SUPPORTING — further observations of the same thing.\n"
            "BASELINE — the prior observation the rule compared against."
        )
    )
    linked_at: datetime
    occurred_at: datetime
    received_at: datetime
    sensor_name: str = Field(description="Which sensor produced this observation.")
    driver: str
    kind: str
    category: str
    severity: str
    summary: str
    source_ip: IPAddressStr = None
    destination_ip: IPAddressStr = None
    destination_port: int | None = None
    protocol: str | None = None
    mac_address: str | None = None
    raw_retained: bool = Field(
        description=(
            "Whether the pre-normalisation form of this observation is still "
            "stored. False means the raw evidence is gone, not that there was none."
        )
    )
    is_simulation: bool


class EvidenceResponse(BaseModel):
    """Everything supporting one finding."""

    finding_id: uuid.UUID
    detection_type: str
    explanation: str
    evidence: dict[str, Any] = Field(description="Structured evidence recorded at detection time.")
    evidence_state: str = Field(description="COMPLETE | PARTIAL | PRUNED")
    observations_recorded: int = Field(
        description="Observations linked to this finding over its lifetime."
    )
    observations_available: int = Field(description="How many of those still exist.")
    observations: list[EvidenceObservation]
    snapshot: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Bounded copies of observations taken at detection time. These "
            "survive event retention and are the reason a finding stays "
            "explainable after its events are pruned."
        ),
    )


class RiskFactorSchema(BaseModel):
    code: str
    label: str
    weight: float = Field(description="Score points this factor contributes at full magnitude.")
    magnitude: float = Field(description="0-1, the normalised strength of this factor.")
    contribution: float = Field(description="weight x magnitude, in score points.")
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class RiskAssessmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    assessed_at: datetime
    score: int
    level: str
    previous_score: int | None = None
    previous_level: str | None = None
    window_started_at: datetime
    window_ended_at: datetime
    weights_fingerprint: str = Field(
        description=(
            "Digest of the weight table in force. Scores computed under "
            "different weights are not comparable."
        )
    )


class DeviceRiskResponse(BaseModel):
    """A device's current risk, with everything needed to argue with it."""

    device_id: uuid.UUID
    state: str = Field(
        description=(
            "ASSESSED — a score has been computed.\n"
            "NOT_ASSESSED — the risk engine has not yet run for this device. "
            "`risk_score` is null, which is not the same as a score of zero."
        )
    )
    risk_score: int | None = None
    risk_level: str = Field(description="UNKNOWN | LOW | MEDIUM | HIGH | CRITICAL")
    risk_updated_at: datetime | None = None
    previous_score: int | None = None
    previous_level: str | None = None
    window_started_at: datetime | None = Field(
        default=None, description="Start of the evidence window this assessment covers."
    )
    window_ended_at: datetime | None = None
    weights_fingerprint: str | None = None
    weights_source: str = Field(
        description="DEFAULT | CONFIGURED | DEFAULT_AFTER_INVALID_CONFIGURATION"
    )
    factors: list[RiskFactorSchema] = Field(
        default_factory=list,
        description=(
            "Every factor the model evaluated, including those that contributed "
            "nothing — 'considered and absent' is information too."
        ),
    )
    contributing_findings: list[FindingSummary] = Field(default_factory=list)
    is_simulation: bool = False


class RiskHistoryPage(BaseModel):
    device_id: uuid.UUID
    items: list[RiskAssessmentSummary]
    total: int


class DetectorStatus(BaseModel):
    id: str
    detection_type: str
    version: int
    description: str
    scope: str = Field(description="REAL_NETWORK | SIMULATION")
    state: str = Field(
        description=(
            "ACTIVE — the rule runs.\n"
            "DISABLED — turned off in the detection configuration.\n"
            "NOT_CONFIGURED — a prerequisite is missing; see `remedy`. This is "
            "never reported as an empty result."
        )
    )
    detail: str | None = None
    remedy: str | None = None
    findings_total: int = Field(description="Findings this rule has produced in this scope.")


class AnalysisStatus(BaseModel):
    state: str = Field(
        description=(
            "NEVER_RUN — no analysis pass has completed.\n"
            "IDLE — caught up; every stored event has been analysed.\n"
            "BACKLOG — events are waiting and a pass ran recently.\n"
            "STALLED — events are waiting and no pass has run recently. Check "
            "that a worker process is running."
        )
    )
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    watermark_event_id: int
    pending_events: int
    events_examined_total: int
    findings_created_total: int
    findings_updated_total: int


class RiskEngineStatus(BaseModel):
    weights_source: str
    weights_detail: str | None = Field(
        default=None,
        description="Why the stored configuration was rejected, when it was.",
    )
    weights_fingerprint: str
    thresholds: dict[str, int]
    devices_assessed: int
    devices_not_assessed: int = Field(
        description="Devices with no score yet. Reported as UNKNOWN, never as zero."
    )


class DetectionStatusResponse(BaseModel):
    analysis: AnalysisStatus
    detectors: list[DetectorStatus]
    risk: RiskEngineStatus
    findings_by_status: dict[str, int]
    findings_by_severity: dict[str, int]
    findings_real: int
    findings_simulation: int


class DetectionConfigResponse(BaseModel):
    source: str = Field(description="DEFAULT | CONFIGURED | DEFAULT_AFTER_INVALID_CONFIGURATION")
    detail: str | None = None
    version: int | None = Field(
        default=None,
        description="Optimistic-concurrency version. Send it back when updating.",
    )
    fingerprint: str
    config: dict[str, Any]


class UpdateDetectionConfigRequest(BaseModel):
    config: dict[str, Any] = Field(
        description=(
            "Partial document, merged into the current configuration and "
            "validated as a whole. Unknown keys are rejected rather than ignored."
        )
    )
    version: int | None = Field(
        default=None,
        description=(
            "The version you read. If it no longer matches, the write is refused "
            "with 409 rather than overwriting someone else's change."
        ),
    )
    reason: str = Field(
        min_length=3,
        max_length=500,
        description="Recorded in the audit log. Changing what the platform detects is security-relevant.",
    )


class TriageFindingRequest(BaseModel):
    status: str = Field(description="ACKNOWLEDGED | RESOLVED | SUPPRESSED | FALSE_POSITIVE | OPEN")
    note: str = Field(
        min_length=3,
        max_length=2000,
        description=(
            "Why. Recorded on the finding and in the audit log — 'what happened' "
            "without 'why' answers half the question you will have later."
        ),
    )
