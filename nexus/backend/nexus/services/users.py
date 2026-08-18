"""User account administration.

Every method here is a privileged operation, so every method audits. The
interesting logic is not the CRUD — it is the guards that stop an administrator
from accidentally making the system unadministrable, and the rules that make
an account change take effect immediately rather than whenever sessions happen
to expire.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.errors import Conflict, NotFound, OperationNotPermitted, ValidationFailed
from nexus.core.logging import get_logger
from nexus.core.passwords import PasswordPolicyError, PasswordService, generate_password
from nexus.core.rbac import Role
from nexus.db.base import utcnow
from nexus.db.models.user import User
from nexus.services.audit import AuditAction, AuditActor, AuditOutcome, AuditService
from nexus.services.auth import AuthService

logger = get_logger(__name__)

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64


@dataclass(frozen=True)
class CreatedUser:
    """A new account plus, when generated, its one-time password.

    The password is returned exactly once, in the response to the creating
    admin, and is never stored in plaintext or written to a log. If it is lost,
    the remedy is a reset, not a recovery — there is nothing to recover.
    """

    user: User
    generated_password: str | None = None


class UserService:
    def __init__(
        self,
        session: AsyncSession,
        passwords: PasswordService,
        audit: AuditService,
        auth: AuthService,
    ) -> None:
        self._session = session
        self._passwords = passwords
        self._audit = audit
        self._auth = auth

    # ---------------------------------------------------------------- reads --

    async def list_users(self) -> list[User]:
        result = await self._session.execute(select(User).order_by(User.username))
        return list(result.scalars().all())

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFound("No such user.")
        return user

    # --------------------------------------------------------------- writes --

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        role: Role,
        actor: AuditActor,
        email: str | None = None,
        password: str | None = None,
        must_change_password: bool = True,
        source_ip: str | None = None,
    ) -> CreatedUser:
        """Create an account.

        When no password is supplied, a strong one is generated and returned
        once. That is the safer default: an admin inventing passwords for other
        people produces predictable passwords, and emailing a password is worse
        than showing it once on screen.
        """
        normalised = _normalise_username(username)
        await self._assert_username_available(normalised)

        generated: str | None = None
        if password is None:
            password = generate_password()
            generated = password

        try:
            password_hash = await self._passwords.hash(password)
        except PasswordPolicyError as exc:
            raise ValidationFailed(str(exc)) from exc

        user = User(
            username=normalised,
            display_name=display_name.strip()[:128] or normalised,
            email=(email or "").strip().lower() or None,
            role=role.value,
            password_hash=password_hash,
            password_changed_at=utcnow(),
            must_change_password=must_change_password,
            is_active=True,
        )
        self._session.add(user)
        await self._session.flush()

        await self._audit.record(
            AuditAction.USER_CREATED,
            actor=actor,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            source_ip=source_ip,
            details={
                "role": role.value,
                # Same naming reason as below: a key containing "password" is
                # redacted by the audit sanitiser, which would throw away a
                # fact worth auditing.
                "force_credential_change": must_change_password,
                # Recorded so an auditor can tell a generated credential from
                # one the admin chose. The password itself is never included.
                # Named "credential_generated" rather than "password_generated"
                # because the audit sanitiser redacts any key containing
                # "password" — correctly, and bluntly. The flag is a fact about
                # the action, so it gets a name the filter does not swallow.
                "credential_generated": generated is not None,
            },
        )
        return CreatedUser(user=user, generated_password=generated)

    async def set_role(
        self,
        user_id: uuid.UUID,
        new_role: Role,
        *,
        actor: AuditActor,
        reason: str,
        source_ip: str | None = None,
    ) -> User:
        """Change a user's role.

        Guarded twice. You cannot demote yourself — an admin who does that by
        accident locks themselves out of fixing it. And you cannot remove the
        last active admin, which would leave the deployment with no way to
        manage users at all short of editing the database by hand.
        """
        user = await self.get_user(user_id)
        previous = user.role

        if str(user.id) == str(actor.user_id) and new_role is not Role.ADMIN:
            raise OperationNotPermitted(
                "You cannot remove your own administrator role. Ask another administrator to do it."
            )
        if previous == Role.ADMIN.value and new_role is not Role.ADMIN:
            await self._assert_not_last_admin(user)

        if previous == new_role.value:
            return user

        user.role = new_role.value
        await self._audit.record(
            AuditAction.USER_ROLE_CHANGED,
            actor=actor,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            reason=reason,
            source_ip=source_ip,
            details={"from": previous, "to": new_role.value},
        )

        # The new role must apply now, not at next login. Revoking the user's
        # sessions forces re-authentication; without this, a demoted operator
        # keeps their old permissions until their session expires — which is
        # exactly the window an insider would use.
        await self._auth.revoke_all_sessions(user, reason="role_changed", actor=actor)
        return user

    async def set_active(
        self,
        user_id: uuid.UUID,
        active: bool,
        *,
        actor: AuditActor,
        reason: str,
        source_ip: str | None = None,
    ) -> User:
        """Deactivate or reactivate an account.

        Deactivation is used instead of deletion: audit rows reference the user,
        and an investigation months later needs to resolve who did what.
        """
        user = await self.get_user(user_id)

        if not active:
            if str(user.id) == str(actor.user_id):
                raise OperationNotPermitted("You cannot deactivate your own account.")
            if user.role == Role.ADMIN.value:
                await self._assert_not_last_admin(user)

        if user.is_active == active:
            return user

        user.is_active = active
        await self._audit.record(
            AuditAction.USER_REACTIVATED if active else AuditAction.USER_DEACTIVATED,
            actor=actor,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            reason=reason,
            source_ip=source_ip,
        )
        if not active:
            # Session validation also checks `is_active`, so this is belt and
            # braces — but it makes the "active sessions" view immediately
            # truthful rather than showing sessions that will fail on next use.
            await self._auth.revoke_all_sessions(user, reason="account_deactivated", actor=actor)
        return user

    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        actor: AuditActor,
        display_name: str | None = None,
        email: str | None = None,
        source_ip: str | None = None,
    ) -> User:
        user = await self.get_user(user_id)
        changes: dict[str, object] = {}

        if display_name is not None and display_name.strip() != user.display_name:
            changes["display_name"] = {"from": user.display_name, "to": display_name.strip()}
            user.display_name = display_name.strip()[:128]

        if email is not None:
            new_email = email.strip().lower() or None
            if new_email != user.email:
                changes["email"] = {"from": user.email, "to": new_email}
                user.email = new_email

        if not changes:
            return user

        await self._audit.record(
            AuditAction.USER_UPDATED,
            actor=actor,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            source_ip=source_ip,
            details=changes,
        )
        return user

    async def reset_password(
        self,
        user_id: uuid.UUID,
        *,
        actor: AuditActor,
        reason: str,
        source_ip: str | None = None,
    ) -> tuple[User, str]:
        """Issue a new one-time password for another user.

        Returns the generated password so the admin can hand it over out of
        band. The account is marked ``must_change_password``, and every session
        the user had is revoked — a password reset is exactly the situation
        where you cannot assume the existing sessions belong to the owner.
        """
        user = await self.get_user(user_id)
        new_password = generate_password()
        user.password_hash = await self._passwords.hash(new_password)
        user.password_changed_at = utcnow()
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None

        await self._audit.record(
            AuditAction.USER_PASSWORD_RESET,
            actor=actor,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            reason=reason,
            source_ip=source_ip,
        )
        await self._auth.revoke_all_sessions(user, reason="password_reset", actor=actor)
        return user, new_password

    async def unlock(
        self,
        user_id: uuid.UUID,
        *,
        actor: AuditActor,
        reason: str,
        source_ip: str | None = None,
    ) -> User:
        """Clear a lockout early, when an operator locked themselves out."""
        user = await self.get_user(user_id)
        user.failed_login_count = 0
        user.locked_until = None
        await self._audit.record(
            AuditAction.USER_UPDATED,
            actor=actor,
            outcome=AuditOutcome.SUCCESS,
            target_type="user",
            target_id=str(user.id),
            target_label=user.username,
            reason=reason,
            source_ip=source_ip,
            details={"action": "unlock"},
        )
        return user

    # ---------------------------------------------------------------- guards --

    async def _assert_username_available(self, username: str) -> None:
        existing = await self._session.execute(select(User.id).where(User.username == username))
        if existing.scalar_one_or_none() is not None:
            raise Conflict("That username is already taken.")

    async def _assert_not_last_admin(self, user: User) -> None:
        result = await self._session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.ADMIN.value, User.is_active.is_(True), User.id != user.id)
        )
        if (result.scalar_one() or 0) == 0:
            raise OperationNotPermitted(
                "This is the last active administrator. Promote another user first."
            )


def _normalise_username(username: str) -> str:
    """Lowercase and validate.

    Case folding happens at exactly one point — here — so "Ian" and "ian"
    cannot become two accounts. The unique index is on the stored value, so the
    guarantee is enforced by the database as well as by this function.
    """
    normalised = username.strip().lower()
    if not USERNAME_MIN_LENGTH <= len(normalised) <= USERNAME_MAX_LENGTH:
        raise ValidationFailed(
            f"Username must be between {USERNAME_MIN_LENGTH} and {USERNAME_MAX_LENGTH} characters."
        )
    if not all(char.isalnum() or char in "._-" for char in normalised):
        raise ValidationFailed(
            "Username may contain only letters, digits, dot, underscore and hyphen."
        )
    return normalised
