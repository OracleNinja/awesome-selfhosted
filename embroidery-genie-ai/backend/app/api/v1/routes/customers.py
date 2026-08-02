"""Module 8 — Customers."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.core.deps import CurrentContext, DbSession
from app.models import Customer, Design, Order, OrderStatus
from app.schemas import CustomerIn, CustomerOut, Message

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("")
def list_customers(
    context: CurrentContext,
    db: DbSession,
    q: str | None = Query(None, max_length=120),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    stmt = select(Customer).where(Customer.organization_id == context.org_id)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Customer.name.ilike(pattern),
                Customer.company.ilike(pattern),
                Customer.email.ilike(pattern),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Customer.name).limit(limit).offset(offset)).all()

    # Order and revenue counts make the list useful rather than a phone book.
    stats = {
        row.customer_id: row
        for row in db.execute(
            select(
                Order.customer_id,
                func.count(Order.id).label("order_count"),
                func.coalesce(func.sum(Order.total), 0).label("revenue"),
            )
            .where(
                Order.organization_id == context.org_id,
                Order.status != OrderStatus.cancelled,
            )
            .group_by(Order.customer_id)
        ).all()
    }

    return {
        "items": [
            {
                **CustomerOut.model_validate(c).model_dump(mode="json"),
                "order_count": int(getattr(stats.get(c.id), "order_count", 0) or 0),
                "revenue": float(getattr(stats.get(c.id), "revenue", 0) or 0),
            }
            for c in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerIn, context: CurrentContext, db: DbSession) -> Customer:
    data = payload.model_dump()
    data["email"] = str(data["email"]) if data.get("email") else None
    customer = Customer(organization_id=context.org_id, **data)
    db.add(customer)
    db.flush()
    return customer


@router.get("/{customer_id}")
def get_customer(customer_id: uuid.UUID, context: CurrentContext, db: DbSession) -> dict:
    customer = _get(db, context, customer_id)
    orders = db.scalars(
        select(Order)
        .where(Order.customer_id == customer.id)
        .order_by(Order.created_at.desc())
        .limit(25)
    ).all()
    designs = db.scalars(
        select(Design)
        .where(Design.customer_id == customer.id)
        .order_by(Design.created_at.desc())
        .limit(25)
    ).all()
    return {
        **CustomerOut.model_validate(customer).model_dump(mode="json"),
        "orders": [
            {
                "id": str(o.id), "number": o.number, "status": o.status.value,
                "total": float(o.total or 0),
                "due_date": o.due_date.isoformat() if o.due_date else None,
            }
            for o in orders
        ],
        "designs": [
            {
                "id": str(d.id), "name": d.name, "status": d.status.value,
                "stitch_count": d.stitch_count,
            }
            for d in designs
        ],
    }


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: uuid.UUID, payload: CustomerIn, context: CurrentContext, db: DbSession
) -> Customer:
    customer = _get(db, context, customer_id)
    data = payload.model_dump()
    data["email"] = str(data["email"]) if data.get("email") else None
    for field, value in data.items():
        setattr(customer, field, value)
    db.flush()
    return customer


@router.delete("/{customer_id}", response_model=Message)
def delete_customer(customer_id: uuid.UUID, context: CurrentContext, db: DbSession) -> Message:
    db.delete(_get(db, context, customer_id))
    return Message(detail="Customer deleted.")


def _get(db: DbSession, context: CurrentContext, customer_id: uuid.UUID) -> Customer:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id, Customer.organization_id == context.org_id
        )
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return customer
