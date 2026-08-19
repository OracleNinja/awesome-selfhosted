"""Devices: the things NEXUS observes on the network.

A device is an *inference*, not a fact. The network shows you frames and
packets; "there is a laptop at 192.168.1.73" is a conclusion drawn from them,
and it can be wrong — MAC addresses are randomised by modern phones, DHCP
reassigns addresses, and a NAT hides many hosts behind one. The schema is built
to be honest about that:

* identity is keyed on the MAC where one was observed, because it survives a
  DHCP lease change, and falls back to IP only when no MAC is available (a
  device seen through a router);
* ``first_seen``/``last_seen`` record what was observed rather than implying
  continuous presence;
* ``vendor`` is populated only from a real OUI database and stays ``NULL``
  otherwise — a guess would be indistinguishable from evidence;
* ``is_simulation`` marks laboratory devices at the storage layer, so nothing
  downstream can mistake a lab host for a real one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexus.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Lifecycle states an operator sees on the inventory screen.
DEVICE_STATES = ("ACTIVE", "IDLE", "QUARANTINED", "RETIRED")

RISK_LEVELS = ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "devices"

    # Normalised to lowercase colon-separated form on write, so the same NIC
    # reported by two sensors in two formats is one device. Nullable because a
    # host seen only through a router has no MAC visible to us — and a nullable
    # unique column in PostgreSQL permits many NULLs, which is exactly right.
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True, unique=True)

    # Most recently observed address. History lives in the event stream; this
    # column is the "where is it now" the inventory screen needs.
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)

    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # NULL means "we have no OUI database loaded", not "unknown manufacturer".
    # The UI renders the two differently: NOT CONFIGURED versus UNKNOWN.
    vendor: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Operator-assigned, and never overwritten by a sensor. A human writing
    # "kitchen thermostat" is worth more than any amount of fingerprinting, and
    # losing it to an automatic update would teach operators not to bother.
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    # Suppresses risk escalation for a device the operator has vouched for.
    # It does not suppress *recording* anything: a trusted device that starts
    # port-scanning still produces events.
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")

    # 0-100 with an explicit level, both computed by the risk engine (M5) and
    # both NULL/UNKNOWN until it has actually run. A score of 0 would claim
    # "assessed, and found safe", which is a different statement from "not yet
    # assessed" — and the difference matters on a security dashboard.
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, server_default="UNKNOWN")
    risk_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Laboratory devices are marked at the storage layer so no query, export, or
    # dashboard can accidentally present simulated data as real observation.
    is_simulation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('ACTIVE', 'IDLE', 'QUARANTINED', 'RETIRED')", name="state_valid"
        ),
        CheckConstraint(
            "risk_level IN ('UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="risk_level_valid",
        ),
        CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="risk_score_range",
        ),
        # A device must be identifiable by something.
        CheckConstraint(
            "mac_address IS NOT NULL OR ip_address IS NOT NULL",
            name="identifiable",
        ),
        # "What is on my network right now", the default inventory sort.
        Index("ix_devices_last_seen_at", last_seen_at.desc()),
        # Lookup during ingestion, which happens on every observation.
        Index("ix_devices_ip_address", "ip_address"),
        # Devices with no MAC (seen only through a router) still need a
        # uniqueness rule, or two ingestion workers racing on the same address
        # create two rows for one host. Partial, so it applies only where the
        # MAC-based unique constraint cannot.
        Index(
            "uq_devices_ip_address_when_no_mac",
            "ip_address",
            unique=True,
            postgresql_where=text("mac_address IS NULL"),
        ),
        # "Show me the risky ones first."
        Index("ix_devices_risk_score", risk_score.desc()),
        Index("ix_devices_state", "state"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Device {self.label or self.hostname or self.mac_address or self.ip_address}>"
