"""Authentication endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.middleware import client_ip
from nexus.api.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    SessionSummary,
    UserSummary,
)
from nexus.api.schemas.common import OkResponse, responses
from nexus.api.security import AuthDep, IdentityDep, actor_from
from nexus.core.errors import NotFound, RateLimited, ValidationFailed
from nexus.core.logging import get_logger
from nexus.core.passwords import PasswordPolicyError
from nexus.core.rbac import Role, permissions_for
from nexus.db.base import utcnow
from nexus.db.models.user import UserSession
from nexus.services.auth import IssuedSession

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Log in and start a session",
    description=(
        "Verifies credentials and sets an HttpOnly session cookie. The returned "
        "`csrf_token` must be echoed in the `X-CSRF-Token` header on every "
        "state-changing request.\n\n"
        "All failures return the same 401 with the same message: distinguishing "
        "'no such user' from 'wrong password' would let anyone enumerate accounts."
    ),
    responses=responses(401, 422, 429, 503),
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    context: ContextDep,
    auth: AuthDep,
) -> LoginResponse:
    source_ip = client_ip(request, context.settings)

    # Keyed on address *and* username so that one attacker cannot lock out a
    # legitimate user's ability to log in from elsewhere simply by burning the
    # username's budget from their own address.
    limiter_key = f"{source_ip or 'unknown'}|{payload.username.strip().lower()[:64]}"
    decision = context.login_rate_limiter.check(limiter_key)
    if not decision.allowed:
        logger.warning("login_rate_limited", extra={"source_ip": source_ip})
        raise RateLimited(
            "Too many login attempts. Wait before trying again.",
            retry_after_seconds=decision.retry_after_seconds,
        )

    user, issued = await auth.authenticate(
        payload.username,
        payload.password,
        source_ip=source_ip,
        user_agent=request.headers.get("user-agent"),
    )

    # A successful login clears the throttle: an operator who mistyped twice
    # then succeeded should not stay penalised.
    context.login_rate_limiter.reset(limiter_key)
    _set_session_cookie(response, context, issued)

    return LoginResponse(
        user=UserSummary.model_validate(user),
        csrf_token=issued.csrf_token,
        expires_at=issued.expires_at,
        permissions=sorted(permissions_for(Role(user.role)), key=lambda item: item.value),
    )


@router.post(
    "/logout",
    response_model=OkResponse,
    summary="End the current session",
    description=(
        "Revokes the session server-side and clears the cookie. Because sessions "
        "are server-side, revocation is immediate and complete — a copy of the "
        "cookie taken earlier stops working too."
    ),
    responses=responses(401, 403),
)
async def logout(
    request: Request,
    response: Response,
    context: ContextDep,
    identity: IdentityDep,
    auth: AuthDep,
) -> OkResponse:
    await auth.revoke_session(
        identity.session,
        reason="logout",
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    _clear_session_cookie(response, context)
    return OkResponse(message="Signed out.")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Describe the current user",
    description=(
        "Used by the frontend on load to decide whether a session is live and "
        "which controls to render. Authorization is still enforced server-side "
        "on every request — this list is for presentation only."
    ),
    responses=responses(401),
)
async def me(identity: IdentityDep) -> MeResponse:
    return MeResponse(
        user=UserSummary.model_validate(identity.user),
        permissions=sorted(permissions_for(identity.role), key=lambda item: item.value),
        session_expires_at=identity.session.expires_at,
    )


@router.post(
    "/password",
    response_model=OkResponse,
    summary="Change your own password",
    description=(
        "Requires the current password even though you are already signed in: "
        "that stops someone who stole a session — but not the password — from "
        "locking the owner out. All *other* sessions for the account are revoked "
        "on success."
    ),
    responses=responses(401, 422),
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    context: ContextDep,
    identity: IdentityDep,
    auth: AuthDep,
) -> OkResponse:
    try:
        await auth.change_own_password(
            identity,
            payload.current_password,
            payload.new_password,
            source_ip=client_ip(request, context.settings),
        )
    except PasswordPolicyError as exc:
        # A policy violation is the caller's input problem, not an auth failure.
        raise ValidationFailed(
            str(exc), details={"fields": [{"field": "body.new_password", "message": str(exc)}]}
        ) from exc
    return OkResponse(message="Password changed. Other sessions have been signed out.")


@router.get(
    "/sessions",
    response_model=list[SessionSummary],
    summary="List your active sessions",
    description=(
        "Shows every live session for your account so you can recognise one that "
        "is not yours. Revoked and expired sessions are excluded."
    ),
    responses=responses(401),
)
async def list_sessions(session: SessionDep, identity: IdentityDep) -> list[SessionSummary]:
    result = await session.execute(
        select(UserSession)
        .where(
            UserSession.user_id == identity.user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .order_by(UserSession.last_seen_at.desc())
    )
    return [
        SessionSummary(
            id=record.id,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            expires_at=record.expires_at,
            ip_address=str(record.ip_address) if record.ip_address else None,
            user_agent=record.user_agent,
            current=record.id == identity.session.id,
        )
        for record in result.scalars().all()
    ]


@router.delete(
    "/sessions/{session_id}",
    response_model=OkResponse,
    summary="Revoke one of your sessions",
    description="Ends another of your own sessions immediately.",
    responses=responses(401, 403, 404),
)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    context: ContextDep,
    identity: IdentityDep,
    auth: AuthDep,
) -> OkResponse:
    record = await session.get(UserSession, session_id)
    # Scoped to the caller's own sessions. Note the identical response for
    # "does not exist" and "belongs to someone else": otherwise this endpoint
    # would confirm the existence of other users' session ids.
    if record is None or record.user_id != identity.user.id:
        raise NotFound("No such session.")

    await auth.revoke_session(
        record,
        reason="revoked_by_user",
        actor=actor_from(identity),
        source_ip=client_ip(request, context.settings),
    )
    if record.id == identity.session.id:
        _clear_session_cookie(response, context)
    return OkResponse(message="Session revoked.")


# ---------------------------------------------------------------- cookies --


def _set_session_cookie(response: Response, context: ContextDep, issued: IssuedSession) -> None:
    """Set the session cookie with every flag that matters.

    * ``httponly`` — JavaScript cannot read it, so an XSS bug cannot steal the
      session.
    * ``secure`` — never sent over plain HTTP (configurable off only outside
      production, for a local bench).
    * ``samesite="strict"`` — the browser will not attach it to requests
      originating from another site, which defeats CSRF at the browser level.
      Strict rather than Lax because NEXUS has no cross-site entry points that
      need to arrive authenticated.
    * ``path="/"`` — one cookie for the API and the SPA.
    * ``max_age`` — matches the session's absolute expiry, so a browser stops
      sending a cookie the server would reject anyway.
    """
    settings = context.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=issued.session_token,
        max_age=settings.session_absolute_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response, context: ContextDep) -> None:
    """Delete the cookie with the same attributes it was set with.

    A browser only replaces a cookie when name, path, and domain all match; a
    mismatched delete leaves the old cookie in place and the user apparently
    signed in.
    """
    settings = context.settings
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
