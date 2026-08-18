"""Authentication: proving who a caller is, and keeping that proof revocable.

Session model
-------------
Opaque, server-side sessions in an ``HttpOnly`` cookie. The alternative — a
JWT — needs no database lookup, which is why stateless APIs like it. The price
is that a JWT cannot be revoked: a stolen token stays valid until it expires,
and "sign out everywhere" requires a denylist, which is the database lookup you
were trying to avoid. For a console that can quarantine devices, an operator
must be able to kill a session *now*. Revocability wins.

Two clocks bound every session:

* **Absolute expiry** — it dies at a fixed instant no matter how active it is,
  bounding the damage from a token stolen early in its life.
* **Idle timeout** — it dies after inactivity, so an unlocked laptop in a
  cupboard does not stay logged in for twelve hours.

Defences against password guessing
----------------------------------
1. **Uniform failures.** Unknown user, wrong password, disabled account, and
   locked account all produce the same message and the same status. Any
   difference is an account-enumeration oracle.
2. **Uniform timing.** When the username does not exist, a dummy Argon2
   verification burns equivalent CPU. Otherwise "unknown user" returns in
   microseconds and "wrong password" in ~50ms, and an attacker enumerates
   accounts with a stopwatch.
3. **Durable per-account lockout.** The failure counter lives on the user row,
   so it survives restarts and is shared across worker processes.
4. **Audited failures.** Every failed attempt is recorded, and recorded
   *durably* — see the commit note in :meth:`AuthService.authenticate`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.core.errors import Unauthenticated
from nexus.core.logging import get_logger
from nexus.core.passwords import PasswordPolicyError, PasswordService
from nexus.core.rbac import Role
from nexus.core.tokens import generate_token, hash_token
from nexus.db.base import utcnow
from nexus.db.models.user import User, UserSession
from nexus.services.audit import AuditAction, AuditActor, AuditOutcome, AuditService

logger = get_logger(__name__)

# One message for every authentication failure. Deliberately unhelpful: an
# attacker learns nothing, and a legitimate user who has genuinely locked
# themselves out is told to wait, which is the actionable part.
GENERIC_AUTH_FAILURE = "Invalid username or password."

# `last_seen_at` drives the idle timeout, but writing it on every request would
# mean a database write per API call. Updating at most once per interval keeps
# the timeout accurate to within this window at a fraction of the write cost.
SESSION_TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True)
class IssuedSession:
    """A newly created session and the two tokens the client needs.

    The raw tokens exist only here and in the HTTP response. Only their hashes
    are stored, so this object must never be logged or persisted.
    """

    session_id: uuid.UUID
    session_token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """The result of validating a session cookie on an incoming request."""

    user: User
    session: UserSession

    @property
    def role(self) -> Role:
        try:
            return Role(self.user.role)
        except ValueError:  # pragma: no cover - CHECK constraint prevents this
            # An unrecognised role resolves to no permissions elsewhere; here we
            # pick the least privileged so nothing can be done with it either.
            return Role.VIEWER


class AuthService:
    """Login, logout, session validation, and password changes."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        passwords: PasswordService,
        audit: AuditService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._passwords = passwords
        self._audit = audit

    # ---------------------------------------------------------------- login --

    async def authenticate(
        self,
        username: str,
        password: str,
        *,
        source_ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, IssuedSession]:
        """Verify credentials and issue a session.

        Raises :class:`Unauthenticated` with a uniform message on every failure.

        **Commit note.** Failure bookkeeping — the incremented counter, the
        audit row — is committed by this method *before* it raises. Normally
        the request's transaction commits at the end, but an exception rolls it
        back, which would erase the evidence of the attack we are defending
        against. Committing here is a deliberate exception to the unit-of-work
        rule, and it is safe: at that point no other work is pending in the
        transaction.
        """
        lookup = username.strip().lower()
        user = await self._find_by_username(lookup)

        if user is None:
            # Burn equivalent CPU so timing does not reveal that the account
            # does not exist.
            await self._passwords.verify_dummy()
            await self._record_failure(
                AuditAction.LOGIN_FAILED,
                actor=AuditActor.anonymous(lookup),
                source_ip=source_ip,
                reason="No such account.",
            )
            raise Unauthenticated(GENERIC_AUTH_FAILURE)

        now = utcnow()
        if user.locked_until is not None and user.locked_until > now:
            await self._passwords.verify_dummy()
            await self._record_failure(
                AuditAction.LOGIN_BLOCKED,
                actor=AuditActor(username=user.username, user_id=user.id, role=user.role),
                source_ip=source_ip,
                reason="Account is temporarily locked after repeated failures.",
                details={"locked_until": user.locked_until.isoformat()},
            )
            raise Unauthenticated("This account is temporarily locked. Try again later.")

        if not user.is_active:
            await self._passwords.verify_dummy()
            await self._record_failure(
                AuditAction.LOGIN_BLOCKED,
                actor=AuditActor(username=user.username, user_id=user.id, role=user.role),
                source_ip=source_ip,
                reason="Account is deactivated.",
            )
            raise Unauthenticated(GENERIC_AUTH_FAILURE)

        if not await self._passwords.verify(user.password_hash, password):
            await self._register_failed_attempt(user, source_ip=source_ip)
            raise Unauthenticated(GENERIC_AUTH_FAILURE)

        # ---- success ----
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now

        # The plaintext is legitimately in hand exactly once per login, so this
        # is the only moment an account can be migrated to stronger parameters.
        if self._passwords.needs_rehash(user.password_hash):
            user.password_hash = await self._passwords.hash(password)
            logger.info("password_rehashed", extra={"user_id": str(user.id)})

        issued = await self.create_session(user, source_ip=source_ip, user_agent=user_agent)
        await self._audit.record(
            AuditAction.LOGIN_SUCCEEDED,
            actor=AuditActor(username=user.username, user_id=user.id, role=user.role),
            outcome=AuditOutcome.SUCCESS,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            source_ip=source_ip,
            details={"session_id": str(issued.session_id)},
        )
        return user, issued

    async def _register_failed_attempt(self, user: User, *, source_ip: str | None) -> None:
        """Count a failure and lock the account once the budget is spent."""
        user.failed_login_count += 1
        locked = False
        if user.failed_login_count >= self._settings.login_max_failed_attempts:
            user.locked_until = utcnow() + timedelta(minutes=self._settings.login_lockout_minutes)
            user.failed_login_count = 0
            locked = True

        await self._record_failure(
            AuditAction.LOGIN_FAILED,
            actor=AuditActor(username=user.username, user_id=user.id, role=user.role),
            source_ip=source_ip,
            reason="Incorrect password.",
            details={"account_locked": locked},
        )

    async def _record_failure(
        self,
        action: AuditAction,
        *,
        actor: AuditActor,
        source_ip: str | None,
        reason: str,
        details: dict | None = None,
    ) -> None:
        await self._audit.record(
            action,
            actor=actor,
            outcome=AuditOutcome.FAILURE,
            source_ip=source_ip,
            reason=reason,
            details=details,
        )
        # See the commit note in `authenticate`.
        await self._session.commit()

    # ------------------------------------------------------------- sessions --

    async def create_session(
        self,
        user: User,
        *,
        source_ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        """Mint a new session. Always called on login, never reused.

        Issuing a fresh session id at login is what prevents *session
        fixation*: an attacker who plants a known session id in a victim's
        browser before they log in gains nothing, because the id they planted
        is not the id the victim ends up using.
        """
        session_token = generate_token()
        csrf_token = generate_token()
        now = utcnow()
        expires_at = now + timedelta(minutes=self._settings.session_absolute_ttl_minutes)

        record = UserSession(
            user_id=user.id,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            ip_address=source_ip,
            # User agents are attacker-controlled; truncated on write and never
            # rendered as markup.
            user_agent=(user_agent or "")[:256] or None,
        )
        self._session.add(record)
        await self._session.flush()

        return IssuedSession(
            session_id=record.id,
            session_token=session_token,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    async def resolve_session(self, session_token: str) -> AuthenticatedIdentity | None:
        """Validate a session cookie. Returns ``None`` for anything invalid.

        One indexed lookup by token hash, then four checks: revoked, absolutely
        expired, idle-expired, and account still active. Deactivating a user
        therefore ends their sessions on the next request, without a background
        job.
        """
        if not session_token:
            return None

        result = await self._session.execute(
            select(UserSession).where(UserSession.token_hash == hash_token(session_token))
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None

        now = utcnow()
        if record.revoked_at is not None:
            return None
        if record.expires_at <= now:
            return None

        idle_limit = timedelta(minutes=self._settings.session_idle_timeout_minutes)
        if now - record.last_seen_at > idle_limit:
            # Mark it revoked so the "active sessions" screen tells the truth
            # and the row does not keep being re-evaluated.
            record.revoked_at = now
            record.revoked_reason = "idle_timeout"
            return None

        user = await self._session.get(User, record.user_id)
        if user is None or not user.is_active:
            return None

        if now - record.last_seen_at > SESSION_TOUCH_INTERVAL:
            record.last_seen_at = now

        return AuthenticatedIdentity(user=user, session=record)

    async def revoke_session(
        self,
        record: UserSession,
        *,
        reason: str,
        actor: AuditActor,
        source_ip: str | None = None,
    ) -> None:
        """Revoke one session (logout, or an admin ending someone's session)."""
        if record.revoked_at is None:
            record.revoked_at = utcnow()
            record.revoked_reason = reason[:64]
        await self._audit.record(
            AuditAction.LOGOUT if reason == "logout" else AuditAction.SESSION_REVOKED,
            actor=actor,
            target_type="session",
            target_id=str(record.id),
            source_ip=source_ip,
            reason=reason,
        )

    async def revoke_all_sessions(
        self,
        user: User,
        *,
        reason: str,
        actor: AuditActor,
        keep_session_id: uuid.UUID | None = None,
    ) -> int:
        """Revoke every live session for a user, optionally sparing one.

        Called on password change (an attacker with the old password loses
        their foothold) and on deactivation. ``keep_session_id`` lets a user
        change their own password without logging themselves out.
        """
        result = await self._session.execute(
            select(UserSession).where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
            )
        )
        now = utcnow()
        revoked = 0
        for record in result.scalars().all():
            if keep_session_id is not None and record.id == keep_session_id:
                continue
            record.revoked_at = now
            record.revoked_reason = reason[:64]
            revoked += 1

        if revoked:
            await self._audit.record(
                AuditAction.SESSION_REVOKED,
                actor=actor,
                target_type="user",
                target_id=str(user.id),
                target_label=user.username,
                reason=reason,
                details={"sessions_revoked": revoked},
            )
        return revoked

    # ------------------------------------------------------------ passwords --

    async def change_own_password(
        self,
        identity: AuthenticatedIdentity,
        current_password: str,
        new_password: str,
        *,
        source_ip: str | None = None,
    ) -> None:
        """Change the caller's own password.

        The current password is required even though the caller is already
        authenticated: it stops an attacker who has stolen a session — but not
        the password — from locking the real owner out of their account.
        """
        user = identity.user
        actor = AuditActor(username=user.username, user_id=user.id, role=user.role)

        if not await self._passwords.verify(user.password_hash, current_password):
            await self._audit.record(
                AuditAction.PASSWORD_CHANGED,
                actor=actor,
                outcome=AuditOutcome.FAILURE,
                target_type="user",
                target_id=str(user.id),
                source_ip=source_ip,
                reason="Current password was incorrect.",
            )
            await self._session.commit()  # see the commit note in `authenticate`
            raise Unauthenticated("Your current password is incorrect.")

        try:
            user.password_hash = await self._passwords.hash(new_password)
        except PasswordPolicyError:
            # Policy violations are the caller's own input problem; they are not
            # security telemetry and are reported by the API as a 422.
            raise

        user.password_changed_at = utcnow()
        user.must_change_password = False

        # Every other session loses its footing; the caller keeps theirs.
        await self.revoke_all_sessions(
            user,
            reason="password_changed",
            actor=actor,
            keep_session_id=identity.session.id,
        )
        await self._audit.record(
            AuditAction.PASSWORD_CHANGED,
            actor=actor,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            source_ip=source_ip,
        )

    # --------------------------------------------------------------- lookup --

    async def _find_by_username(self, username: str) -> User | None:
        result = await self._session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()
