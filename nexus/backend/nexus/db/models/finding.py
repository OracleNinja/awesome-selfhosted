"""Security findings, their evidence, and device risk assessments.

A *detection* is the act of a rule firing. A **finding** is the persisted
record of it, and it is the thing an operator triages, so it needs a stable
identity that survives the rule firing again.

Identity: the fingerprint
-------------------------
``fingerprint`` is a deterministic digest of what the finding *is about* — the
detector, the device, the specific transition or endpoint, and whether the
underlying data is simulated. It is ``UNIQUE``, which is what makes re-analysis
safe: the job queue delivers at least once (see :mod:`nexus.services.jobs`), so
a retried analysis pass re-derives the same fingerprint and the insert becomes
an update of ``last_observed_at`` rather than a second row.

Application-level "does it exist already?" checks are not enough here. Two
workers analysing overlapping windows both read "no", both insert, and the
duplicate exists. The constraint is in the database because that is the only
place the check and the write are atomic.

Evidence: the link table
------------------------
``finding_observations`` has ``PRIMARY KEY (finding_id, event_id)``. That
composite key is the second half of the idempotency guarantee: an observation
can support a finding exactly once, so ``observation_count`` cannot be inflated
by re-processing, and neither can any risk factor derived from it.

The link is ``ON DELETE CASCADE`` from ``security_events`` because retention
prunes events. A finding therefore keeps a bounded snapshot of its evidence in
``evidence`` and a monotonic ``observation_count``; comparing that count with
the number of links still present is how the API reports honestly that some
evidence has aged out rather than pretending the finding was always this thin.

Risk assessments
----------------
``device_risk_assessments`` is append-only and written **only when the computed
score or its factors change**. That makes it both a history ("why did this
device's risk go up on Tuesday?") and a no-op under re-computation, which is
what keeps the whole pipeline idempotent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# What kind of thing was detected. Closed here, VARCHAR + CHECK in the database:
# adding a detector is a migration, which is deliberate — a detection type that
# no documentation describes is a detection type nobody can act on.
DETECTION_TYPES = (
    "NEW_DEVICE",
    "IDENTITY_CHANGE",
    "REPEATED_ANOMALY",
    "UNEXPECTED_COMMUNICATION",
)

# Triage lifecycle.
#
# RESOLVED and SUPPRESSED and FALSE_POSITIVE all stop contributing to risk, but
# they mean different things and the difference is worth keeping: "I fixed it",
# "I do not want to see this again", and "the rule was wrong". Collapsing them
# would destroy the only feedback signal about detector quality.
FINDING_STATUSES = (
    "OPEN",
    "ACKNOWLEDGED",
    "RESOLVED",
    "SUPPRESSED",
    "FALSE_POSITIVE",
)

# Statuses whose findings still count towards a device's risk.
ACTIVE_FINDING_STATUSES = ("OPEN", "ACKNOWLEDGED")

# Why an observation is attached to a finding.
OBSERVATION_ROLES = (
    "TRIGGER",  # the observation that made the rule fire
    "SUPPORTING",  # further observations of the same thing
    "BASELINE",  # prior observation the rule compared against
)


class Finding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One detection, persisted."""

    __tablename__ = "findings"

    # Deterministic identity. See the module docstring: this column is the
    # reason re-analysis cannot duplicate work.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Which rule produced this, and which version of it. The version is part of
    # the record because a rule that is retuned produces different conclusions
    # from the same evidence, and an investigator six months later needs to know
    # which logic applied.
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    detection_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Severity: how bad this would be *if the conclusion is correct*.
    # Confidence: how sure the detector is that the conclusion is correct.
    #
    # These are independent axes and the schema keeps them that way. A HIGH
    # severity / LOW confidence finding is entirely valid and common — "this
    # looks like ARP spoofing, but it is also what a DHCP lease change looks
    # like". Multiplying them into one number at storage time destroys exactly
    # the information an analyst needs to prioritise.
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # The finding's own contribution to risk, on the same 0-100 scale as a
    # device's. NULL means the scoring engine has not run for it.
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OPEN")

    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    device_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    # Monotonic count of observations ever linked. Incremented only when a link
    # is genuinely inserted, so a replayed analysis pass cannot inflate it. The
    # API compares it against the number of links still present to report when
    # evidence has been pruned by retention.
    observation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Structured, machine-readable evidence: the values the rule compared, the
    # thresholds it applied, and a bounded snapshot of the observations. This is
    # what lets the finding still be explained after retention removes the
    # underlying events.
    evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Plain English, generated from the same values as `evidence`. Written at
    # detection time rather than rendered in the UI so that the explanation and
    # the evidence cannot drift apart across releases.
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    # The factors that produced `risk_score`, preserved verbatim: each one
    # carries its code, weight, magnitude and contribution. A score without its
    # factors is an unexplainable number.
    risk_factors: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Inherited from the observations that produced it. A finding derived from
    # laboratory data can never be presented as an observation of the real
    # network, and the fingerprint includes this flag so the two can never
    # collapse into one row.
    is_simulation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    triaged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triage_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "detection_type IN ('NEW_DEVICE', 'IDENTITY_CHANGE', 'REPEATED_ANOMALY', "
            "'UNEXPECTED_COMMUNICATION')",
            name="detection_type_valid",
        ),
        CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="severity_valid",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="confidence_range"),
        CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="risk_score_range",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'SUPPRESSED', 'FALSE_POSITIVE')",
            name="status_valid",
        ),
        CheckConstraint("observation_count >= 0", name="observation_count_non_negative"),
        CheckConstraint(
            "last_observed_at >= first_observed_at",
            name="observation_window_ordered",
        ),
        # The triage screen: open work, most recently active first.
        Index("ix_findings_status_last_observed_at", "status", last_observed_at.desc()),
        # A device's findings, for its profile page.
        Index("ix_findings_device_id_last_observed_at", "device_id", last_observed_at.desc()),
        Index("ix_findings_detection_type", "detection_type"),
        Index("ix_findings_detector", "detector"),
        # "What should I look at first" — partial, because only active findings
        # are ever ranked this way.
        Index(
            "ix_findings_open_risk_score",
            risk_score.desc(),
            postgresql_where=text("status IN ('OPEN', 'ACKNOWLEDGED')"),
        ),
        # Real-network views exclude simulation by default, as everywhere else.
        Index(
            "ix_findings_real_last_observed_at",
            last_observed_at.desc(),
            postgresql_where=text("is_simulation = false"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Finding {self.detection_type} {self.severity}/{self.confidence} {self.status}>"


class FindingObservation(Base):
    """Which observations support which finding.

    No surrogate key: the pair *is* the identity, and making it the primary key
    is what stops an observation being counted towards a finding twice.
    """

    __tablename__ = "finding_observations"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("security_events.id", ondelete="CASCADE"),
        primary_key=True,
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="SUPPORTING")
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('TRIGGER', 'SUPPORTING', 'BASELINE')",
            name="role_valid",
        ),
        # "Which findings cite this event?" — the reverse direction, which the
        # primary key's index cannot serve.
        Index("ix_finding_observations_event_id", "event_id"),
    )


class DeviceRiskAssessment(Base):
    """One computed risk score for one device, with its full derivation.

    Append-only, and appended only when the result differs from the previous
    one. Re-running the scoring engine over unchanged inputs produces an
    identical score and writes nothing, which is what makes the risk pipeline
    safe under at-least-once job delivery.
    """

    __tablename__ = "device_risk_assessments"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    device_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)

    # What it was before this assessment. Stored rather than derived by joining
    # to the previous row, so "why did this go up?" is answerable from one row
    # even after older assessments are pruned.
    previous_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_level: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # The explicit contributing factors: code, label, weight, magnitude,
    # contribution, and the evidence each was derived from.
    factors: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    # Which findings were in scope, so the conclusion can be traced back to
    # individual detections and from there to individual observations.
    contributing_finding_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # The correlation window this assessment considered.
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Digest of the weight table in force. Two scores computed under different
    # weights are not comparable, and without this there is no way to tell.
    weights_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    is_simulation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        CheckConstraint(
            "level IN ('UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="level_valid",
        ),
        CheckConstraint(
            "window_ended_at >= window_started_at",
            name="window_ordered",
        ),
        Index("ix_device_risk_assessments_device_id_assessed_at", "device_id", assessed_at.desc()),
    )


class DetectionState(Base):
    """Where the analysis pass has got to.

    A single row per stream holding the highest ``security_events.id`` that has
    been analysed. Detection is a **watermark** over a monotonic id rather than
    a queue of event ids, for three reasons:

    * it is O(1) state regardless of backlog size;
    * a crash mid-pass loses no work, because the watermark only advances on
      commit — the events are simply analysed again, and the fingerprint
      constraints make that harmless;
    * it cannot silently skip events, because ids are assigned by a single
      sequence and the pass consumes them in order.

    The trade-off is that an event inserted with a *lower* id after the
    watermark has passed it would be missed. That cannot happen here: ids come
    from ``GENERATED ALWAYS AS IDENTITY`` and the pass only advances to the
    highest id it actually read, never to ``max(id)`` of the table.
    """

    __tablename__ = "detection_state"

    stream: Mapped[str] = mapped_column(String(32), primary_key=True)

    last_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    events_examined: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    findings_created: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    findings_updated: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("last_event_id >= 0", name="last_event_id_non_negative"),
        CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN ('SUCCEEDED', 'FAILED')",
            name="last_run_status_valid",
        ),
    )


# The only stream that exists today. Named rather than hard-coded at call sites
# so a second one (a replay stream, say) is a value rather than a schema change.
EVENT_STREAM = "security_events"
