"""User administration endpoints. Every route requires `users:manage`."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.middleware import client_ip
from nexus.api.schemas.admin import (
    ChangeRoleRequest,
    CreateUserRequest,
    CreateUserResponse,
    ReasonRequest,
    ResetPasswordResponse,
    SetActiveRequest,
    UpdateUserRequest,
)
from nexus.api.schemas.auth import UserSummary
from nexus.api.schemas.common import responses
from nexus.api.security import AuditDep, AuthDep, actor_from, require
from nexus.core.rbac import Permission
from nexus.services.auth import AuthenticatedIdentity
from nexus.services.users import UserService

router = APIRouter(prefix="/users", tags=["administration"])

# One dependency, declared once, applied to every route below. A new endpoint
# added to this module is protected by construction rather than by remembering.
AdminDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.USERS_MANAGE))]


def get_user_service(
    session: SessionDep, context: ContextDep, audit: AuditDep, auth: AuthDep
) -> UserService:
    return UserService(session, context.passwords, audit, auth)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.get(
    "",
    response_model=list[UserSummary],
    summary="List user accounts",
    responses=responses(401, 403),
)
async def list_users(identity: AdminDep, users: UserServiceDep) -> list[UserSummary]:
    return [UserSummary.model_validate(user) for user in await users.list_users()]


@router.post(
    "",
    response_model=CreateUserResponse,
    status_code=201,
    summary="Create a user account",
    description=(
        "Omit `password` to have a strong one generated and returned once — the "
        "recommended path, since passwords chosen by an admin for someone else "
        "are predictable and passwords sent by email are worse."
    ),
    responses=responses(401, 403, 409, 422),
)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    identity: AdminDep,
    context: ContextDep,
    users: UserServiceDep,
) -> CreateUserResponse:
    created = await users.create_user(
        username=payload.username,
        display_name=payload.display_name,
        role=payload.role,
        email=payload.email,
        password=payload.password,
        must_change_password=payload.must_change_password,
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    return CreateUserResponse(
        user=UserSummary.model_validate(created.user),
        generated_password=created.generated_password,
    )


@router.get(
    "/{user_id}",
    response_model=UserSummary,
    summary="Get one user account",
    responses=responses(401, 403, 404),
)
async def get_user(user_id: uuid.UUID, identity: AdminDep, users: UserServiceDep) -> UserSummary:
    return UserSummary.model_validate(await users.get_user(user_id))


@router.patch(
    "/{user_id}",
    response_model=UserSummary,
    summary="Update a user's profile",
    description="Display name and email only. Role and status have their own endpoints "
    "because they are security-relevant and require a reason.",
    responses=responses(401, 403, 404, 422),
)
async def update_user(
    user_id: uuid.UUID,
    payload: UpdateUserRequest,
    request: Request,
    identity: AdminDep,
    context: ContextDep,
    users: UserServiceDep,
) -> UserSummary:
    user = await users.update_profile(
        user_id,
        display_name=payload.display_name,
        email=payload.email,
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    return UserSummary.model_validate(user)


@router.put(
    "/{user_id}/role",
    response_model=UserSummary,
    summary="Change a user's role",
    description=(
        "Requires a reason, which is recorded in the audit log. All of the "
        "user's sessions are revoked so the new role takes effect immediately "
        "rather than whenever their current session happens to expire.\n\n"
        "Refused if it would remove your own admin role or the last active "
        "administrator."
    ),
    responses=responses(401, 403, 404, 422),
)
async def change_role(
    user_id: uuid.UUID,
    payload: ChangeRoleRequest,
    request: Request,
    identity: AdminDep,
    context: ContextDep,
    users: UserServiceDep,
) -> UserSummary:
    user = await users.set_role(
        user_id,
        payload.role,
        reason=payload.reason,
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    return UserSummary.model_validate(user)


@router.put(
    "/{user_id}/active",
    response_model=UserSummary,
    summary="Activate or deactivate a user",
    description=(
        "Deactivation is the offboarding path; accounts are never deleted, "
        "because audit history references them. Sessions are revoked "
        "immediately. Refused for your own account or the last active admin."
    ),
    responses=responses(401, 403, 404, 422),
)
async def set_active(
    user_id: uuid.UUID,
    payload: SetActiveRequest,
    request: Request,
    identity: AdminDep,
    context: ContextDep,
    users: UserServiceDep,
) -> UserSummary:
    user = await users.set_active(
        user_id,
        payload.is_active,
        reason=payload.reason,
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    return UserSummary.model_validate(user)


@router.post(
    "/{user_id}/password-reset",
    response_model=ResetPasswordResponse,
    summary="Reset a user's password",
    description=(
        "Generates a new one-time password, returns it once, forces a change at "
        "next login, and revokes every session the user had."
    ),
    responses=responses(401, 403, 404, 422),
)
async def reset_password(
    user_id: uuid.UUID,
    payload: ReasonRequest,
    request: Request,
    identity: AdminDep,
    context: ContextDep,
    users: UserServiceDep,
) -> ResetPasswordResponse:
    user, password = await users.reset_password(
        user_id,
        reason=payload.reason,
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    return ResetPasswordResponse(user=UserSummary.model_validate(user), generated_password=password)


@router.post(
    "/{user_id}/unlock",
    response_model=UserSummary,
    summary="Clear a login lockout",
    description="Clears the failed-attempt counter and lockout timer early.",
    responses=responses(401, 403, 404, 422),
)
async def unlock(
    user_id: uuid.UUID,
    payload: ReasonRequest,
    request: Request,
    identity: AdminDep,
    context: ContextDep,
    users: UserServiceDep,
) -> UserSummary:
    user = await users.unlock(
        user_id,
        reason=payload.reason,
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    return UserSummary.model_validate(user)
