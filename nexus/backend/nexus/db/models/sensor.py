"""Sensors: configured sources of observations.

A row here is an operator's declaration that "this source should be running".
Whether it *is* running is runtime state, reported by the sensor manager and
mirrored onto the row so the UI and the health endpoint can show it without
reaching into another process.

Secrets never live in ``config``. A sensor that needs a credential — a router's
API token, say — stores the *name* of an environment variable, and the manager
resolves it at start. Otherwise the credential ends up in a database backup, in
an audit detail blob, and on an admin screen.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexus.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

SENSOR_STATUSES = (
    "STOPPED",  # not running, and not meant to be
    "STARTING",
    "RUNNING",
    # A finite source that ran to completion — a laboratory scenario playing
    # out, for instance. Distinct from STOPPED (nobody started it) and from
    # FAILED, and emphatically not a fault: reporting a finished scenario as an
    # outage is how a dashboard teaches people to ignore red.
    "COMPLETED",
    "DEGRADED",  # running, but not producing what it should
    "FAILED",  # tried to run and could not
    "NOT_AVAILABLE",  # the platform cannot support it here (missing capability)
    "NOT_CONFIGURED",  # enabled but missing required configuration
)


class Sensor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sensors"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Which driver implements it: "arp_discovery", "pcap", "syslog",
    # "lab_synthetic". Validated against the driver registry at write time; kept
    # as a string so adding a driver does not need a migration.
    driver: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Driver-specific settings, validated against the driver's own schema before
    # being written. Never secrets — see the module docstring.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="STOPPED")
    # Why it is in that status, in words an operator can act on. Populated for
    # every non-running status, because "FAILED" with no reason is a dead end.
    status_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # A sensor that is "RUNNING" but has not reported in ten minutes is not
    # running. The heartbeat is what turns a claimed status into a checkable one.
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Lifetime counters, for the ingestion-rate panel and for spotting a sensor
    # that is running but silent.
    events_ingested: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    events_dropped: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    # Marks a sensor that produces laboratory data. Every event it creates is
    # stamped is_simulation, and the flag lives here so the honesty guarantee
    # is a property of configuration rather than of each event's code path.
    is_simulation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('STOPPED', 'STARTING', 'RUNNING', 'COMPLETED', 'DEGRADED', "
            "'FAILED', 'NOT_AVAILABLE', 'NOT_CONFIGURED')",
            name="status_valid",
        ),
        Index("ix_sensors_enabled_status", "enabled", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Sensor {self.name} driver={self.driver} status={self.status}>"
