"""Identity: user accounts and their sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nexus.core.rbac import Role
from nexus.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An operator account.

    Passwords are never stored — only an Argon2id verifier (see
    :mod:`nexus.core.passwords`). The column is named ``password_hash`` and is
    covered by the log redaction list, so it cannot be logged even if a whole
    row is dumped into a log record.
    """

    __tablename__ = "users"

    # Stored lowercased by the service layer; the unique index is on the raw
    # column, so case-folding at exactly one point (account creation and login
    # lookup) is what prevents "Ian" and "ian" becoming two accounts.
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Bumped whenever the hashing parameters change, so a background task can
    # find accounts that need a rehash on next successful login.
    password_algorithm: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="argon2id"
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    role: Mapped[Role] = mapped_column(String(16), nullable=False)

    # Disabling is preferred over deleting: audit rows reference the user, and
    # an incident investigation months later needs to resolve who did what.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Throttling state for online password guessing. Counters live on the row
    # rather than in memory so a restart does not reset an attacker's budget
    # and so all workers share one view.
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'OPERATOR', 'VIEWER')",
            name="role_valid",
        ),
        CheckConstraint("char_length(username) >= 3", name="username_length"),
        # The database enforces the role vocabulary as well as the application.
        # Two layers, because a future maintenance script writing directly to
        # SQL will not import the Python enum.
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.username} role={self.role}>"


class UserSession(Base, UUIDPrimaryKeyMixin):
    """A logged-in browser session.

    Design choice: **opaque server-side sessions, not JWTs.**

    A JWT is self-contained, so the server can validate it without a database
    lookup — attractive for horizontally scaled stateless APIs. The cost is
    that it cannot be revoked: a stolen token stays valid until it expires,
    and "log out everywhere" becomes impossible without a denylist, which is
    the database lookup you were avoiding. For a security console where an
    operator must be able to kill a session *now*, revocability wins.

    What is stored is the SHA-256 of the token, never the token. The cookie
    holds a high-entropy random string; the database holds its digest. A
    database dump therefore does not hand the attacker live sessions, the same
    reasoning as password hashing — though a fast hash is correct here because
    the input is 256 bits of randomness, not a guessable human secret.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 64 hex chars of SHA-256. Unique, so a lookup is a single index probe.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Double-submit CSRF token, also stored hashed for the same reason.
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # Absolute expiry: a session dies at this instant no matter how active it
    # is, which bounds the damage from a token stolen early in its life.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Captured for the "active sessions" screen so an operator can recognise a
    # session that is not theirs. Truncated on write; a user agent is
    # attacker-controlled input and must not be trusted or rendered raw.
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        # Every request validates a session, so this is the hottest lookup in
        # the system: filter by user and skip already-revoked rows.
        Index("ix_user_sessions_user_id_expires_at", "user_id", "expires_at"),
        # Supports the retention job that deletes expired sessions.
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UserSession user_id={self.user_id} expires_at={self.expires_at}>"
