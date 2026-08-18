"""Authentication request and response models."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nexus.core.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from nexus.core.rbac import Permission, Role


class LoginRequest(BaseModel):
    # Length caps are a denial-of-service control, not a policy statement:
    # Argon2's cost scales with input, so an unbounded password field lets one
    # request tie up a worker thread for a long time.
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserSummary(BaseModel):
    """A user as the API presents them.

    This model is an allow-list: ``password_hash`` exists on the ORM object and
    cannot appear here, because only declared fields are serialised.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    email: str | None = None
    role: Role
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime


class LoginResponse(BaseModel):
    user: UserSummary
    # Sent in the body, not in a readable cookie: the SPA holds it in memory
    # and echoes it in the X-CSRF-Token header. Keeping it out of storage means
    # it does not survive a tab being closed, which is the intent.
    csrf_token: str = Field(description="Echo this in the X-CSRF-Token header on writes.")
    expires_at: datetime = Field(description="Absolute session expiry (UTC).")
    permissions: list[Permission] = Field(
        description="Effective permissions, so the UI can hide what the user cannot do. "
        "The server enforces them regardless of what the UI shows."
    )


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None = None
    user_agent: str | None = None
    current: bool = Field(default=False, description="True for the session making this request.")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class MeResponse(BaseModel):
    user: UserSummary
    permissions: list[Permission]
    session_expires_at: datetime
