"""The audit log: an append-only, hash-chained record of sensitive actions.

What tamper-evidence means here
-------------------------------
Each row stores a SHA-256 over its own canonical content *plus the previous
row's hash*. That makes the log a hash chain: changing or deleting row N
invalidates the hash of row N+1, and every row after it. An auditor can walk
the chain and find the exact row where it breaks.

What this does **not** defend against, stated plainly because a security
control you misunderstand is worse than none:

* An attacker with write access to the database can recompute the chain from
  the edited row forward. The chain proves *consistency*, not authenticity.
  Defending against that needs an anchor outside the database — an append-only
  mirror on a separate filesystem, a periodic checkpoint hash sent to a remote
  log, or signatures with a key the database server does not hold. NEXUS
  supports the mirror and the checkpoint; see SECURITY.md for how they are
  configured and what each is worth.
* It does not stop a legitimate ADMIN from performing an action; it records
  that they did.

Why an append-only table rather than "just log to a file"
---------------------------------------------------------
Audit questions are relational: "every quarantine of this device by this user
last month". Files answer that with grep and hope. The database answers it with
an index, and the same transaction that performs the action writes the audit
row, so an action can never be committed without its audit entry.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus.db.base import Base

# The chain's first link. A fixed, obviously-synthetic value so verification
# can recognise the genesis row without a special case in the data.
GENESIS_HASH = "0" * 64


class AuditEvent(Base):
    """One security-sensitive action.

    Rows are never updated or deleted by application code. The retention job is
    the single exception: it prunes from the *oldest* end and records that it
    did so as an audit event of its own, preserving the property that the chain
    is unbroken from the retention watermark forward.
    """

    __tablename__ = "audit_events"

    # BIGINT identity, not UUID: the chain needs a total order, and a monotonic
    # key gives it for free while keeping inserts at the right edge of the index.
    # GENERATED ALWAYS (not BY DEFAULT) means even a direct SQL INSERT cannot
    # choose an id, so nobody can wedge a forged row between two existing links.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # ON DELETE SET NULL, and the username is *copied* rather than joined:
    # deleting a user must not erase the history of what they did, and a
    # rename must not rewrite it either. The snapshot is the historical truth.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Free-form string rather than a database enum: new action types arrive with
    # every feature, and ALTER TYPE on a hot enum column is a migration hazard.
    # The canonical vocabulary lives in nexus.services.audit.AuditAction.
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Human-readable identity of the target at the time of the action
    # ("192.168.1.73", "rule: SSH brute force"), for the same reason the
    # username is copied.
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    # Why the operator says they did it. Required by the API for sensitive
    # actions — an audit trail without intent answers "what" but never "why".
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ties the audit row to every log line emitted while handling the request.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    # Structured extras: before/after values, the parameters of the action.
    # JSONB rather than JSON so it can be indexed and queried by key.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint("outcome IN ('SUCCESS', 'FAILURE', 'DENIED')", name="outcome_valid"),
        # The audit screen's default view: newest first.
        Index("ix_audit_events_occurred_at", occurred_at.desc()),
        # "What did this user do?" and "who touched this device?"
        Index("ix_audit_events_actor_user_id_occurred_at", "actor_user_id", occurred_at.desc()),
        Index("ix_audit_events_action_occurred_at", "action", occurred_at.desc()),
        Index("ix_audit_events_target", "target_type", "target_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AuditEvent {self.id} {self.action} by={self.actor_username} outcome={self.outcome}>"
        )
