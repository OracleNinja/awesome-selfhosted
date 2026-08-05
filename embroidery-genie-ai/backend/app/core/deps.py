"""FastAPI dependencies: authentication, tenancy, and plan gating."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import AuthenticatedUser, AuthError, dev_user, user_from_token
from app.db.session import get_db
from app.models import Membership, OrgRole, Organization, PlanTier, Subscription, User
from app.services import plans

ROLE_RANK = {
    OrgRole.viewer: 0,
    OrgRole.operator: 1,
    OrgRole.digitizer: 2,
    OrgRole.admin: 3,
    OrgRole.owner: 4,
}


@dataclass
class Context:
    """Everything a request handler needs about who is calling."""

    user: User
    organization: Organization
    membership: Membership
    auth: AuthenticatedUser

    @property
    def org_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def role(self) -> OrgRole:
        return self.membership.role

    def can(self, minimum: OrgRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'.",
        )
    return parts[1].strip()


def get_auth(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedUser:
    """Verify the Supabase JWT.

    ``ALLOW_DEV_AUTH`` short-circuits this for local work; it is rejected at
    startup in production so it can never be left on by accident.
    """
    if settings.allow_dev_auth and not authorization:
        return dev_user()
    try:
        return user_from_token(_bearer(authorization))
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return (slug or "workspace")[:60]


def _unique_slug(db: Session, base: str) -> str:
    slug = _slugify(base)
    candidate = slug
    suffix = 1
    while db.scalar(select(Organization.id).where(Organization.slug == candidate)):
        suffix += 1
        candidate = f"{slug}-{suffix}"
        if suffix > 50:  # pragma: no cover - pathological
            candidate = f"{slug}-{uuid.uuid4().hex[:8]}"
            break
    return candidate


def provision(db: Session, auth: AuthenticatedUser) -> Context:
    """Just-in-time provisioning.

    Supabase owns identity; the first authenticated request creates the local
    user row, a personal workspace and a free subscription.  This means there
    is no signup webhook to keep in sync.
    """
    user = db.get(User, uuid.UUID(auth.id)) if _is_uuid(auth.id) else None
    if user is None:
        user = db.scalar(select(User).where(User.email == auth.email)) if auth.email else None

    if user is None:
        user = User(
            id=uuid.UUID(auth.id) if _is_uuid(auth.id) else uuid.uuid4(),
            email=auth.email or f"{auth.id}@users.noreply.embroiderygenie.ai",
            full_name=(auth.claims or {}).get("user_metadata", {}).get("full_name"),
            avatar_url=(auth.claims or {}).get("user_metadata", {}).get("avatar_url"),
        )
        db.add(user)
        db.flush()

    user.last_seen_at = datetime.now(timezone.utc)

    membership = db.scalar(
        select(Membership)
        .where(Membership.user_id == user.id)
        .order_by(Membership.is_default.desc(), Membership.created_at.asc())
    )
    if membership is None:
        organization = Organization(
            name=(user.full_name or user.email.split("@")[0] or "My") + "'s Workspace",
            slug=_unique_slug(db, user.email.split("@")[0]),
            currency=settings.default_currency,
        )
        db.add(organization)
        db.flush()
        membership = Membership(
            user_id=user.id, organization_id=organization.id,
            role=OrgRole.owner, is_default=True,
        )
        db.add(membership)
        db.add(Subscription(organization_id=organization.id, tier=PlanTier.free))
        db.flush()
    else:
        organization = db.get(Organization, membership.organization_id)

    return Context(user=user, organization=organization, membership=membership, auth=auth)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def get_context(
    db: Annotated[Session, Depends(get_db)],
    auth: Annotated[AuthenticatedUser, Depends(get_auth)],
    x_organization_id: Annotated[str | None, Header()] = None,
) -> Context:
    """Resolve the caller and the workspace this request acts on."""
    context = provision(db, auth)

    if x_organization_id and _is_uuid(x_organization_id):
        requested = uuid.UUID(x_organization_id)
        if requested != context.organization.id:
            membership = db.scalar(
                select(Membership).where(
                    Membership.user_id == context.user.id,
                    Membership.organization_id == requested,
                )
            )
            if membership is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of that workspace.",
                )
            context = Context(
                user=context.user,
                organization=db.get(Organization, requested),
                membership=membership,
                auth=auth,
            )
    return context


CurrentContext = Annotated[Context, Depends(get_context)]
DbSession = Annotated[Session, Depends(get_db)]


def require_role(minimum: OrgRole):
    def dependency(context: CurrentContext) -> Context:
        if not context.can(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {minimum.value} role or higher.",
            )
        return context

    return dependency


def require_production(context: CurrentContext, db: DbSession) -> Context:
    """Production management is a Business-plan module."""
    try:
        plans.assert_production_module(db, context.org_id)
    except plans.FeatureLocked as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc
    return context


ProductionContext = Annotated[Context, Depends(require_production)]
