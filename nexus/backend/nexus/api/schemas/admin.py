"""Administration request and response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexus.api.schemas.auth import UserSummary
from nexus.api.schemas.common import IPAddressStr
from nexus.core.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from nexus.core.rbac import Role

# Sensitive actions require a written reason. It is the difference between an
# audit log that says what happened and one that says why — and "why" is the
# only part that helps six months later.
REASON_MIN_LENGTH = 3
REASON_MAX_LENGTH = 500


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, examples=["ian"])
    display_name: str = Field(min_length=1, max_length=128, examples=["Ian"])
    role: Role
    email: str | None = Field(default=None, max_length=255)
    password: str | None = Field(
        default=None,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description=(
            "Optional. When omitted, a strong password is generated and returned "
            "once in the response — the recommended path."
        ),
    )
    must_change_password: bool = Field(
        default=True,
        description="Force a password change at first login.",
    )


class CreateUserResponse(BaseModel):
    user: UserSummary
    generated_password: str | None = Field(
        default=None,
        description=(
            "Shown exactly once and never stored in plaintext. If it is lost, "
            "reset the password — it cannot be recovered."
        ),
    )


class UpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)


class ChangeRoleRequest(BaseModel):
    role: Role
    reason: str = Field(min_length=REASON_MIN_LENGTH, max_length=REASON_MAX_LENGTH)


class SetActiveRequest(BaseModel):
    is_active: bool
    reason: str = Field(min_length=REASON_MIN_LENGTH, max_length=REASON_MAX_LENGTH)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=REASON_MIN_LENGTH, max_length=REASON_MAX_LENGTH)


class ResetPasswordResponse(BaseModel):
    user: UserSummary
    generated_password: str = Field(description="Hand this to the user out of band. Shown once.")


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    actor_username: str
    actor_user_id: uuid.UUID | None = None
    actor_role: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    target_label: str | None = None
    outcome: str
    reason: str | None = None
    request_id: str | None = None
    source_ip: IPAddressStr = None
    details: dict[str, Any] = Field(default_factory=dict)
    entry_hash: str = Field(description="This entry's position in the hash chain.")
    prev_hash: str


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_cursor: int | None = Field(
        default=None,
        description="Pass as `before_id` to fetch the next (older) page.",
    )
    has_more: bool


class ChainVerificationResponse(BaseModel):
    ok: bool = Field(description="False means the log has been altered since it was written.")
    entries_checked: int
    first_invalid_id: int | None = None
    detail: str | None = None
    external_anchor: str = Field(
        description=(
            "Whether an off-database anchor is configured: CONFIGURED or "
            "NOT_CONFIGURED. Without one, the chain proves internal consistency "
            "but cannot prove the whole log was not rewritten by someone with "
            "database write access."
        )
    )
