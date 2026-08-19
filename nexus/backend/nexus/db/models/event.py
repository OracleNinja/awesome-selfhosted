"""Security events: the normalised record of everything observed.

This is the table that grows. A home network produces thousands of
observations an hour; over a 90-day retention window that is millions of rows,
and it must stay queryable the whole time. Three decisions follow from that:

**Monotonic primary key.** ``BIGINT GENERATED ALWAYS AS IDENTITY``. Inserts
land at the right-hand edge of the index instead of scattering across it the
way random UUIDs would, so the hot part of the index stays small enough to sit
in memory.

**A BRIN index on ``received_at``.** A B-tree index on a timestamp column of a
10-million-row table is hundreds of megabytes. A BRIN index stores only the
min/max timestamp per 128-page block — kilobytes — and works because rows
arrive in time order, so blocks are naturally clustered by time. "Events from
last Tuesday" then reads only the blocks whose ranges overlap that day. It is
the right index for append-only time-series data and the wrong one for anything
randomly ordered.

**Normalisation up front.** Every sensor's output is mapped into the same
columns at ingestion. Querying "all traffic to 10.0.0.5" must not require
knowing whether the observation came from packet capture or syslog.

The raw observation is kept alongside the normalised form, bounded in size. It
is the evidence: if normalisation was wrong, an investigator needs what was
actually seen, not our interpretation of it.

Why not table partitioning (yet)
--------------------------------
Declarative partitioning by month would make retention a ``DROP TABLE`` instead
of a bulk ``DELETE``, and would let old months be dropped instantly. It also
complicates every migration, requires a partition-creation job, and pays off at
a scale a home network does not reach. The BRIN index plus a batched retention
job handles this workload; partitioning is the documented upgrade path, and the
schema is compatible with it (all queries carry a time predicate).
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
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus.db.base import Base

# Broad grouping, stable over time — what a dashboard filters on.
EVENT_CATEGORIES = (
    "DISCOVERY",  # a device appeared, changed address, or went quiet
    "TRAFFIC",  # observed flows and connections
    "LOG",  # forwarded log lines (syslog and friends)
    "SCAN",  # port/host scanning behaviour
    "AUTH",  # authentication activity observed on the network
    "ANOMALY",  # statistical deviation from a learned baseline
    "SYSTEM",  # NEXUS talking about itself: sensor up/down, jobs
)

SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # Idempotency key, computed by the sensor from the observation's own
    # identity. Re-delivery after a restart or a retry hits the unique index and
    # is discarded rather than double-counted. Getting this wrong inflates every
    # metric and every detection threshold downstream.
    event_uid: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, unique=True)

    # When it happened on the network, per the sensor.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When NEXUS stored it. The two differ under backlog or clock skew, and the
    # difference is itself a signal — a sensor whose events arrive an hour late
    # is a sensor with a problem.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    sensor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sensors.id", ondelete="SET NULL"), nullable=True
    )
    # Snapshot of which sensor produced this, surviving the sensor's deletion —
    # same reasoning as the audit log's actor_username.
    sensor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    driver: Mapped[str] = mapped_column(String(32), nullable=False)

    category: Mapped[str] = mapped_column(String(16), nullable=False)
    # Dotted specific type: "device.discovered", "flow.observed",
    # "syslog.message". Free-form so a new sensor needs no migration.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, server_default="INFO")

    # 0-100. How much the sensor trusts its own conclusion — a MAC seen in an
    # ARP reply is near-certain; a device type inferred from traffic patterns
    # is not. Carried through to detections so a high-confidence low-severity
    # event is distinguishable from a guess.
    confidence: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("100")
    )

    device_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    # Network 5-tuple, as far as it is known. INET is a real PostgreSQL type:
    # it validates on write, sorts correctly, and supports subnet containment
    # operators, so "everything from 192.168.1.0/24" is an index-usable query
    # rather than a string prefix match.
    source_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    destination_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    source_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)

    # One line a human can read in a list without expanding anything.
    summary: Mapped[str] = mapped_column(String(500), nullable=False)

    # Normalised structured detail: byte counts, log facility, TTL — whatever
    # the event kind defines. JSONB so it is queryable and indexable.
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # The observation as received, truncated at ingestion. This is evidence: if
    # normalisation got it wrong, an investigator needs what was actually seen.
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    # THE honesty flag. Laboratory traffic is real data about a real (isolated)
    # network, but it is not the operator's home network, and every query,
    # export, and dashboard must be able to tell them apart.
    is_simulation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('DISCOVERY', 'TRAFFIC', 'LOG', 'SCAN', 'AUTH', 'ANOMALY', 'SYSTEM')",
            name="category_valid",
        ),
        CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="severity_valid",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="confidence_range"),
        CheckConstraint(
            "source_port IS NULL OR (source_port >= 0 AND source_port <= 65535)",
            name="source_port_range",
        ),
        CheckConstraint(
            "destination_port IS NULL OR (destination_port >= 0 AND destination_port <= 65535)",
            name="destination_port_range",
        ),
        # The event feed, newest first.
        Index("ix_security_events_occurred_at", occurred_at.desc()),
        # A device's timeline — the query behind every device profile page.
        Index("ix_security_events_device_id_occurred_at", "device_id", occurred_at.desc()),
        # Dashboard filters.
        Index("ix_security_events_category_occurred_at", "category", occurred_at.desc()),
        Index("ix_security_events_severity_occurred_at", "severity", occurred_at.desc()),
        # Cheap time-range scans over the append-only column; see the module
        # docstring for why BRIN and not B-tree.
        Index(
            "ix_security_events_received_at_brin",
            "received_at",
            postgresql_using="brin",
            postgresql_with={"pages_per_range": 128},
        ),
        # "Everything involving this address", including subnet containment.
        Index("ix_security_events_source_ip", "source_ip"),
        Index("ix_security_events_destination_ip", "destination_ip"),
        # Ad-hoc queries into the normalised detail without a migration per
        # question. jsonb_path_ops is smaller and faster than the default
        # operator class for containment (@>) queries, which is what we run.
        Index(
            "ix_security_events_attributes",
            "attributes",
            postgresql_using="gin",
            postgresql_ops={"attributes": "jsonb_path_ops"},
        ),
        # Real-network views exclude simulation by default, so the predicate is
        # on nearly every query and deserves to be indexed with the timestamp.
        Index(
            "ix_security_events_real_occurred_at",
            occurred_at.desc(),
            postgresql_where=text("is_simulation = false"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SecurityEvent {self.id} {self.kind} {self.severity}>"
