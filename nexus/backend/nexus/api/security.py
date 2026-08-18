"""Request-level authentication, CSRF, and authorization.

These are FastAPI dependencies, which is the point: a route declares what it
needs (``identity: IdentityDep``, ``_: Depends(require(Permission.RULES_WRITE))``)
and cannot execute unless that requirement was satisfied. Compare with checking
inside the handler, where forgetting the check is a silently unprotected
endpoint.

CSRF, and why a cookie needs it
-------------------------------
Browsers attach cookies to a request based on its *destination*, not its
origin. So if an operator with a live NEXUS session visits a malicious page,
that page can make the browser POST to
``https://nexus.local/api/v1/devices/x/quarantine`` — and the session cookie
rides along. The server sees a perfectly authenticated request that the
operator never intended. That is cross-site request forgery.

Three layers stop it here:

1. **``SameSite=Strict`` on the session cookie.** The browser refuses to send
   it on cross-site requests at all. This alone defeats the classic attack in
   any current browser; the remaining layers cover older clients and the case
   where a future feature needs a laxer policy.
2. **A CSRF token that must be echoed in a header.** Issued at login, stored
   *hashed* server-side alongside the session. An attacking page cannot read
   the token (it is not in a readable cookie, and the same-origin policy stops
   it reading our responses), so it cannot construct a valid request. Requiring
   a custom header is itself a barrier: a browser will not send one
   cross-origin without a successful CORS preflight, which NEXUS does not grant
   to unknown origins.
3. **Comparison in constant time**, against the stored hash, so the check
   cannot be turned into a timing oracle.

Read-only requests are exempt because they change nothing — that is the whole
reason GET is defined as safe, and it is why no state-changing operation may
ever be exposed as a GET.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.middleware import client_ip
from nexus.core.context import AppContext
from nexus.core.errors import CSRFError, PermissionDenied, Unauthenticated
from nexus.core.logging import bind_user, get_logger
from nexus.core.rbac import Permission, Role, has_permission
from nexus.core.tokens import constant_time_compare, hash_token
from nexus.services.audit import AuditAction, AuditActor, AuditOutcome, AuditService
from nexus.services.auth import AuthenticatedIdentity, AuthService

logger = get_logger(__name__)

CSRF_HEADER = "X-CSRF-Token"

# Methods that must not change state, and therefore need no CSRF token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


# --------------------------------------------------------------- services --


def get_audit_service(session: SessionDep, context: ContextDep) -> AuditService:
    return AuditService(session, context.settings)


AuditDep = Annotated[AuditService, Depends(get_audit_service)]


def get_auth_service(session: SessionDep, context: ContextDep, audit: AuditDep) -> AuthService:
    return AuthService(session, context.settings, context.passwords, audit)


AuthDep = Annotated[AuthService, Depends(get_auth_service)]


# --------------------------------------------------------- authentication --


async def get_identity(
    request: Request,
    context: ContextDep,
    auth: AuthDep,
) -> AuthenticatedIdentity:
    """Resolve and validate the caller's session, enforcing CSRF.

    CSRF is checked here rather than in a separate dependency so that no
    authenticated write endpoint can exist without it. A route that wants a
    user gets the check for free; a route that wants to skip it has to say so
    explicitly, which is visible in review.
    """
    token = request.cookies.get(context.settings.session_cookie_name, "")
    identity = await auth.resolve_session(token)
    if identity is None:
        raise Unauthenticated()

    # Every log line for the rest of this request carries the user id.
    bind_user(str(identity.user.id))

    if request.method not in SAFE_METHODS:
        _verify_csrf(request, identity)

    return identity


def _verify_csrf(request: Request, identity: AuthenticatedIdentity) -> None:
    supplied = request.headers.get(CSRF_HEADER, "")
    if not supplied:
        raise CSRFError("Missing CSRF token.")
    if not constant_time_compare(hash_token(supplied), identity.session.csrf_token_hash):
        logger.warning(
            "csrf_validation_failed",
            extra={"path": request.url.path, "method": request.method},
        )
        raise CSRFError()


async def get_optional_identity(
    request: Request,
    context: ContextDep,
    auth: AuthDep,
) -> AuthenticatedIdentity | None:
    """Identity if present, ``None`` if not. For endpoints that serve both.

    Still enforces CSRF when a session *is* present and the method is unsafe —
    an endpoint being open to anonymous callers is not a reason to let an
    authenticated one be forged.
    """
    token = request.cookies.get(context.settings.session_cookie_name, "")
    if not token:
        return None
    identity = await auth.resolve_session(token)
    if identity is None:
        return None
    bind_user(str(identity.user.id))
    if request.method not in SAFE_METHODS:
        _verify_csrf(request, identity)
    return identity


IdentityDep = Annotated[AuthenticatedIdentity, Depends(get_identity)]
OptionalIdentityDep = Annotated["AuthenticatedIdentity | None", Depends(get_optional_identity)]


# ---------------------------------------------------------- authorization --


def require(
    permission: Permission,
) -> Callable[..., Coroutine[Any, Any, AuthenticatedIdentity]]:
    """Build a dependency that demands one permission.

    Used as ``identity: Annotated[AuthenticatedIdentity, Depends(require(P.X))]``,
    so the route both receives the caller and cannot run without the check.

    A denial is audited. Refused attempts are the interesting ones — a VIEWER
    repeatedly trying to quarantine devices is a signal, and a system that only
    records successes cannot show it to you.
    """

    async def dependency(
        request: Request,
        identity: IdentityDep,
        context: ContextDep,
        session: SessionDep,
        audit: AuditDep,
    ) -> AuthenticatedIdentity:
        role = identity.role
        if has_permission(role, permission):
            return identity

        await audit.record(
            AuditAction.PERMISSION_DENIED,
            actor=AuditActor(
                username=identity.user.username,
                user_id=identity.user.id,
                role=role,
            ),
            outcome=AuditOutcome.DENIED,
            target_type="permission",
            target_id=permission.value,
            reason=f"Role {role.value} lacks {permission.value}.",
            source_ip=client_ip(request, context.settings),
            details={"path": request.url.path, "method": request.method},
        )
        # The denial must survive the exception that follows; see the commit
        # note in nexus.services.auth.AuthService.authenticate.
        await session.commit()

        raise PermissionDenied(
            "You do not have permission to perform this action.",
            details={"required_permission": permission.value, "your_role": role.value},
        )

    return dependency


def actor_from(identity: AuthenticatedIdentity) -> AuditActor:
    """Convenience: build an audit actor from a resolved identity."""
    return AuditActor(
        username=identity.user.username,
        user_id=identity.user.id,
        role=identity.role,
    )


def require_role(identity: AuthenticatedIdentity, role: Role) -> None:
    """Assert an exact role. Used sparingly — permissions are the normal tool.

    Present for the rare rule that is genuinely about identity rather than
    capability, such as "only an ADMIN may edit another ADMIN".
    """
    if identity.role is not role:
        raise PermissionDenied(details={"required_role": role.value})


__all__ = [
    "CSRF_HEADER",
    "AuditDep",
    "AuthDep",
    "IdentityDep",
    "OptionalIdentityDep",
    "actor_from",
    "get_identity",
    "require",
    "require_role",
]


# Re-exported for routes that need the raw session alongside identity.
SessionAlias = AsyncSession
ContextAlias = AppContext
