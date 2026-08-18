"""Audit logging: recording who did what, and making alteration detectable.

The guarantee
-------------
An audit entry is written **in the same database transaction as the action it
describes**. There is no code path that quarantines a device and then fails to
record it, because both live or die together. This is why the session context
manager owns the commit (see :mod:`nexus.db.session`) — callers cannot commit
the action separately from its evidence.

The hash chain
--------------
Each row stores::

    entry_hash = SHA256(canonical_json(fields) ‖ prev_hash)

where ``prev_hash`` is the previous row's ``entry_hash``. Altering row *N*
changes its hash, which breaks row *N+1*'s stored ``prev_hash``, and so on to
the end of the table. :meth:`AuditService.verify_chain` walks the chain and
reports the first row where the recomputed hash disagrees.

Concurrency
-----------
Two transactions appending at once would both read the same ``prev_hash`` and
fork the chain. Appends therefore take a PostgreSQL **transaction-scoped
advisory lock**: a named lock, held for the duration of the transaction,
released automatically on commit *or* rollback — including when the process is
killed, because the lock lives with the connection. Audit volume is low, so
serialising appends costs nothing that matters.

What the chain does not protect against
---------------------------------------
Anyone who can write to the database can recompute the chain from the edited
row forward. The chain proves *internal consistency*, not authenticity. Two
optional anchors outside the database narrow that gap:

* **The mirror** (``NEXUS_AUDIT_MIRROR_PATH``): every entry is also appended to
  a file that can live on a different filesystem, owned by a different user,
  or on append-only storage. An attacker must then tamper in two places.
* **Checkpoints**: the hash of the newest entry can be exported and shipped
  elsewhere, so any rewrite of history before that point is detectable.

Both are documented in SECURITY.md with what they are and are not worth. If
neither is configured, the audit log reports that honestly rather than implying
protection it does not have.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.core.logging import get_logger, request_id_var
from nexus.core.rbac import Role
from nexus.db.base import utcnow
from nexus.db.models.audit import GENESIS_HASH, AuditEvent

logger = get_logger(__name__)

# A fixed key for the advisory lock. Derived from ASCII "NEXUSAUD" so that a
# collision with another application's advisory lock on the same database is
# vanishingly unlikely, and so the number is recognisable in pg_locks.
AUDIT_LOCK_KEY = 0x4E45585553415544  # 5640181712861135684


class AuditAction(str, Enum):
    """The vocabulary of auditable actions.

    A closed vocabulary in Python, an open ``VARCHAR`` in the database. New
    actions ship without a migration, while this enum keeps call sites honest
    and gives the UI a stable list to filter by.
    """

    # Authentication
    LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGIN_BLOCKED = "LOGIN_BLOCKED"  # account locked or rate limited
    LOGOUT = "LOGOUT"
    SESSION_REVOKED = "SESSION_REVOKED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"

    # Account administration
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_ROLE_CHANGED = "USER_ROLE_CHANGED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    USER_REACTIVATED = "USER_REACTIVATED"
    USER_PASSWORD_RESET = "USER_PASSWORD_RESET"

    # Authorization
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # Configuration
    SETTING_CHANGED = "SETTING_CHANGED"
    RETENTION_CHANGED = "RETENTION_CHANGED"

    # Platform lifecycle
    SYSTEM_STARTED = "SYSTEM_STARTED"
    ADMIN_BOOTSTRAPPED = "ADMIN_BOOTSTRAPPED"
    AUDIT_CHAIN_VERIFIED = "AUDIT_CHAIN_VERIFIED"
    AUDIT_PRUNED = "AUDIT_PRUNED"


class AuditOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"  # attempted, did not work
    DENIED = "DENIED"  # refused by authorization or a safety rule


@dataclass(frozen=True)
class AuditActor:
    """Who performed an action.

    ``user_id`` is optional because some auditable events have no user: a
    failed login for a username that does not exist, a retention job, the
    bootstrap command. The username is always recorded, so every row answers
    "who" even when it cannot answer "which account".
    """

    username: str
    user_id: Any | None = None
    role: Role | str | None = None

    @classmethod
    def system(cls, name: str = "system") -> AuditActor:
        """The platform acting on its own behalf (jobs, startup, retention)."""
        return cls(username=name, user_id=None, role=None)

    @classmethod
    def anonymous(cls, attempted_username: str | None = None) -> AuditActor:
        """An unauthenticated caller. Used for failed logins."""
        return cls(username=attempted_username or "<anonymous>", user_id=None, role=None)


@dataclass(frozen=True)
class ChainVerification:
    """Result of walking the hash chain."""

    ok: bool
    entries_checked: int
    first_invalid_id: int | None = None
    detail: str | None = None


class AuditService:
    """Appends and verifies audit entries.

    Holds a session rather than creating one: the whole point is to be part of
    the caller's transaction.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def record(
        self,
        action: AuditAction | str,
        *,
        actor: AuditActor,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        target_type: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        reason: str | None = None,
        source_ip: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append one entry to the chain.

        Returns the persisted (flushed, not committed) entry. The caller's
        transaction decides whether it survives, which is the guarantee: if the
        action rolls back, so does its audit row, and the log never claims
        something happened that did not.
        """
        # Serialise appends. Transaction-scoped, so it is released on commit or
        # rollback with no unlock call to forget, and no lock leaked if the
        # process dies mid-transaction.
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUDIT_LOCK_KEY}
        )

        prev_hash = await self._latest_hash()
        occurred_at = utcnow()
        safe_details = _sanitise_details(details or {})

        action_value = _action_value(action)
        outcome_value = outcome.value if isinstance(outcome, AuditOutcome) else str(outcome)
        payload: dict[str, Any] = {
            "occurred_at": occurred_at.isoformat(),
            "actor_username": actor.username,
            "actor_user_id": str(actor.user_id) if actor.user_id else None,
            "actor_role": _role_value(actor.role),
            "action": action_value,
            "target_type": target_type,
            "target_id": target_id,
            "target_label": target_label,
            "outcome": outcome_value,
            "reason": reason,
            "request_id": request_id_var.get(),
            "source_ip": source_ip,
            "details": safe_details,
        }
        entry_hash = compute_entry_hash(payload, prev_hash)

        entry = AuditEvent(
            occurred_at=occurred_at,
            actor_user_id=actor.user_id,
            actor_username=actor.username[:64],
            actor_role=_role_value(actor.role),
            action=action_value[:64],
            target_type=target_type[:32] if target_type else None,
            target_id=target_id[:128] if target_id else None,
            target_label=target_label[:255] if target_label else None,
            outcome=outcome_value,
            reason=reason,
            request_id=payload["request_id"],
            source_ip=source_ip,
            details=safe_details,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self._session.add(entry)
        await self._session.flush()

        # Structured log as well as the table: the log is what a shipper
        # forwards off-box in real time, and the table is what queries.
        logger.info(
            "audit",
            extra={
                "audit_action": action_value,
                "audit_outcome": outcome_value,
                "actor": actor.username,
                "target_type": target_type,
                "target_id": target_id,
            },
        )
        _mirror(self._settings, payload | {"entry_hash": entry_hash, "prev_hash": prev_hash})
        return entry

    async def _latest_hash(self) -> str:
        """The newest entry's hash, or the genesis value for an empty log."""
        result = await self._session.execute(
            select(AuditEvent.entry_hash).order_by(AuditEvent.id.desc()).limit(1)
        )
        return result.scalar_one_or_none() or GENESIS_HASH

    async def verify_chain(self, *, limit: int | None = None) -> ChainVerification:
        """Recompute every hash and report the first inconsistency.

        Starts from the oldest *remaining* row, whose ``prev_hash`` is accepted
        as a watermark: retention legitimately prunes the oldest end, and a
        verifier that demanded the genesis row would report tampering every time
        the retention job ran.
        """
        statement = select(AuditEvent).order_by(AuditEvent.id.asc())
        if limit is not None:
            statement = statement.limit(limit)
        entries = (await self._session.execute(statement)).scalars().all()

        if not entries:
            return ChainVerification(ok=True, entries_checked=0, detail="Audit log is empty.")

        expected_prev = entries[0].prev_hash
        for index, entry in enumerate(entries):
            if entry.prev_hash != expected_prev:
                return ChainVerification(
                    ok=False,
                    entries_checked=index,
                    first_invalid_id=entry.id,
                    detail="Entry does not link to the previous entry.",
                )
            recomputed = compute_entry_hash(_payload_from_entry(entry), entry.prev_hash)
            if recomputed != entry.entry_hash:
                return ChainVerification(
                    ok=False,
                    entries_checked=index,
                    first_invalid_id=entry.id,
                    detail="Entry content does not match its recorded hash.",
                )
            expected_prev = entry.entry_hash

        return ChainVerification(ok=True, entries_checked=len(entries))


def compute_entry_hash(payload: dict[str, Any], prev_hash: str) -> str:
    """Hash a canonical rendering of the entry, chained to its predecessor.

    Canonicalisation matters more than the hash function here. ``json.dumps``
    with ``sort_keys=True`` and fixed separators produces the same bytes for the
    same content on any Python version and any machine — without that, a
    verifier could recompute a *different* hash for an untouched row and report
    false tampering, which would make the whole mechanism untrustworthy.
    """
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    digest = hashlib.sha256()
    digest.update(canonical.encode("utf-8"))
    digest.update(prev_hash.encode("utf-8"))
    return digest.hexdigest()


def _payload_from_entry(entry: AuditEvent) -> dict[str, Any]:
    """Rebuild the exact dictionary that was hashed when the entry was written."""
    return {
        "occurred_at": _isoformat(entry.occurred_at),
        "actor_username": entry.actor_username,
        "actor_user_id": str(entry.actor_user_id) if entry.actor_user_id else None,
        "actor_role": entry.actor_role,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "target_label": entry.target_label,
        "outcome": entry.outcome,
        "reason": entry.reason,
        "request_id": entry.request_id,
        "source_ip": entry.source_ip,
        "details": entry.details,
    }


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def _role_value(role: Role | str | None) -> str | None:
    if role is None:
        return None
    return role.value if isinstance(role, Role) else str(role)


def _action_value(action: AuditAction | str) -> str:
    return action.value if isinstance(action, AuditAction) else str(action)


# Keys that must never be written into an audit detail blob, even by mistake.
# The audit log is the one table an operator reads casually and exports freely,
# so a credential landing here would spread furthest.
_FORBIDDEN_DETAIL_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "password_hash",
    "csrf",
    "cookie",
    "api_key",
)

_MAX_DETAIL_BYTES = 16 * 1024


def _sanitise_details(details: dict[str, Any]) -> dict[str, Any]:
    """Strip forbidden keys and cap the size of the details blob.

    The cap exists because ``details`` is caller-supplied and JSONB rows are
    read back into memory during verification; an unbounded blob would let one
    action make the audit view unusable.
    """
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        key_str = str(key)
        if any(fragment in key_str.lower() for fragment in _FORBIDDEN_DETAIL_KEYS):
            cleaned[key_str] = "[redacted]"
        else:
            cleaned[key_str] = value

    encoded = json.dumps(cleaned, default=str)
    if len(encoded.encode("utf-8")) > _MAX_DETAIL_BYTES:
        return {
            "truncated": True,
            "reason": "details exceeded the size limit",
            "keys": sorted(cleaned)[:50],
        }
    return cleaned


def _mirror(settings: Settings, payload: dict[str, Any]) -> None:
    """Append the entry to the optional off-database mirror.

    Best-effort by design. The database row is the record of truth and is
    already durable within the caller's transaction; a mirror that could roll
    back the action it describes would mean a full disk on a secondary log
    prevents an operator from responding to an incident. A failure is logged
    loudly instead, and the health endpoint reports the mirror as degraded.
    """
    path = settings.audit_mirror_path
    if path is None:
        return
    try:
        mirror_path = Path(path)
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        # Line-delimited JSON opened in append mode: writes under the typical
        # 4 KiB pipe/-page boundary are atomic enough that concurrent appenders
        # do not interleave partial lines, and appends never rewrite history.
        with mirror_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    except OSError as exc:
        logger.error(
            "audit_mirror_write_failed",
            extra={"error": exc.__class__.__name__, "path": str(path)},
        )
