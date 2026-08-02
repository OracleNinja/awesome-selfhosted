"""Account, workspace and subscription endpoints.

Sign-in itself happens client-side against Supabase; the backend only ever
sees the resulting JWT.  These endpoints expose the product state that hangs
off that identity.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import CurrentContext, DbSession
from app.models import Membership, Organization
from app.schemas import MeOut, Message, ProfileUpdate
from app.services import plans

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=MeOut)
def me(context: CurrentContext, db: DbSession) -> MeOut:
    """The caller, their active workspace, and what their plan allows."""
    memberships = db.scalars(
        select(Membership).where(Membership.user_id == context.user_id)
    ).all()
    org_ids = [m.organization_id for m in memberships]
    orgs = {
        o.id: o
        for o in db.scalars(select(Organization).where(Organization.id.in_(org_ids))).all()
    } if org_ids else {}

    return MeOut(
        user=context.user,
        organization=context.organization,
        role=context.role.value,
        subscription=plans.quota_status(db, context.org_id),
        memberships=[
            {
                "organization_id": str(m.organization_id),
                "name": orgs[m.organization_id].name if m.organization_id in orgs else "",
                "slug": orgs[m.organization_id].slug if m.organization_id in orgs else "",
                "role": m.role.value,
                "is_active": m.organization_id == context.org_id,
            }
            for m in memberships
        ],
    )


@router.patch("/me", response_model=MeOut)
def update_profile(payload: ProfileUpdate, context: CurrentContext, db: DbSession) -> MeOut:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(context.user, field, value)
    db.flush()
    return me(context, db)


@router.get("/plans")
def available_plans() -> dict:
    return {"plans": plans.list_plans()}


@router.get("/usage")
def usage(context: CurrentContext, db: DbSession) -> dict:
    return plans.quota_status(db, context.org_id)


@router.post("/workspace/switch/{organization_id}", response_model=Message)
def switch_workspace(organization_id: str, context: CurrentContext, db: DbSession) -> Message:
    """Mark a workspace as the caller's default.

    The active workspace for any single request is chosen by the
    ``X-Organization-Id`` header; this only changes which one is picked when
    the header is absent.
    """
    memberships = db.scalars(
        select(Membership).where(Membership.user_id == context.user_id)
    ).all()
    target = next((m for m in memberships if str(m.organization_id) == organization_id), None)
    if target is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="You are not a member of that workspace.")
    for membership in memberships:
        membership.is_default = membership.id == target.id
    db.flush()
    return Message(detail="Default workspace updated.")
