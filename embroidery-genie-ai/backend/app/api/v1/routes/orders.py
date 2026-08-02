"""Module 8 — Orders, work orders and production status.

Status is a state machine, not a free-text field.  Illegal transitions are
rejected, and every change writes an audit event — a production floor needs to
be able to answer "who moved this to sewing and when".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentContext, DbSession, ProductionContext
from app.models import (
    Design,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    Product,
)
from app.schemas import Message, OrderIn, OrderStatusUpdate

router = APIRouter(prefix="/orders", tags=["orders"])

# Legal moves. Anything can be cancelled; nothing comes back from delivered.
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.quote: {OrderStatus.approved, OrderStatus.cancelled},
    OrderStatus.approved: {OrderStatus.digitizing, OrderStatus.sewing, OrderStatus.cancelled},
    OrderStatus.digitizing: {OrderStatus.sewing, OrderStatus.approved, OrderStatus.cancelled},
    OrderStatus.sewing: {OrderStatus.completed, OrderStatus.digitizing, OrderStatus.cancelled},
    OrderStatus.completed: {OrderStatus.delivered, OrderStatus.sewing},
    OrderStatus.delivered: set(),
    OrderStatus.cancelled: {OrderStatus.quote},
}


def _next_number(db: DbSession, org_id: uuid.UUID) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"EG-{year}-"
    last = db.scalar(
        select(func.max(Order.number)).where(
            Order.organization_id == org_id, Order.number.like(f"{prefix}%")
        )
    )
    sequence = 1
    if last:
        try:
            sequence = int(str(last).rsplit("-", 1)[-1]) + 1
        except ValueError:
            sequence = 1
    return f"{prefix}{sequence:04d}"


def _recalculate(order: Order) -> None:
    subtotal = sum(item.line_total for item in order.items)
    cost = sum(float(item.unit_cost or 0) * item.quantity for item in order.items)
    order.subtotal = round(subtotal, 2)
    order.cost_total = round(cost, 2)
    order.total = round(subtotal - float(order.discount or 0) + float(order.tax or 0), 2)


def _serialize(order: Order) -> dict:
    return {
        "id": str(order.id),
        "number": order.number,
        "status": order.status.value,
        "customer_id": str(order.customer_id) if order.customer_id else None,
        "customer_name": order.customer.name if order.customer else None,
        "due_date": order.due_date.isoformat() if order.due_date else None,
        "rush": order.rush,
        "notes": order.notes,
        "placement": order.placement,
        "assigned_machine_id": (
            str(order.assigned_machine_id) if order.assigned_machine_id else None
        ),
        "subtotal": float(order.subtotal or 0),
        "discount": float(order.discount or 0),
        "tax": float(order.tax or 0),
        "total": float(order.total or 0),
        "cost_total": float(order.cost_total or 0),
        "profit": round(order.profit, 2),
        "margin_pct": round(order.profit / float(order.total) * 100, 1)
        if float(order.total or 0) > 0
        else 0.0,
        "quantity": sum(item.quantity for item in order.items),
        "items": [
            {
                "id": str(item.id),
                "design_id": str(item.design_id) if item.design_id else None,
                "design_name": item.design.name if item.design else None,
                "stitch_count": item.design.stitch_count if item.design else None,
                "product_id": str(item.product_id) if item.product_id else None,
                "product_sku": item.product.sku if item.product else None,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price or 0),
                "unit_cost": float(item.unit_cost or 0),
                "line_total": round(item.line_total, 2),
                "placement": item.placement,
                "pricing_breakdown": item.pricing_breakdown,
            }
            for item in order.items
        ],
        "events": [
            {
                "id": str(e.id),
                "from_status": e.from_status,
                "to_status": e.to_status,
                "note": e.note,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in order.events
        ],
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


def _get(db: DbSession, context: CurrentContext, order_id: uuid.UUID) -> Order:
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.items).selectinload(OrderItem.design),
            selectinload(Order.items).selectinload(OrderItem.product),
            selectinload(Order.events),
            selectinload(Order.customer),
        )
        .where(Order.id == order_id, Order.organization_id == context.org_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    return order


@router.get("")
def list_orders(
    context: ProductionContext,
    db: DbSession,
    status_filter: str | None = Query(None, alias="status"),
    customer_id: uuid.UUID | None = None,
    overdue: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    stmt = select(Order).where(Order.organization_id == context.org_id)
    if status_filter:
        try:
            stmt = stmt.where(Order.status == OrderStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown status.") from exc
    if customer_id:
        stmt = stmt.where(Order.customer_id == customer_id)
    if overdue:
        stmt = stmt.where(
            Order.due_date < date.today(),
            Order.status.notin_([OrderStatus.delivered, OrderStatus.cancelled]),
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(
            selectinload(Order.items).selectinload(OrderItem.design),
            selectinload(Order.items).selectinload(OrderItem.product),
            selectinload(Order.events),
            selectinload(Order.customer),
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [_serialize(o) for o in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/board")
def production_board(context: ProductionContext, db: DbSession) -> dict:
    """Kanban view: orders grouped by production status."""
    rows = db.scalars(
        select(Order)
        .options(
            selectinload(Order.items).selectinload(OrderItem.design),
            selectinload(Order.items).selectinload(OrderItem.product),
            selectinload(Order.events),
            selectinload(Order.customer),
        )
        .where(
            Order.organization_id == context.org_id,
            Order.status != OrderStatus.cancelled,
        )
        .order_by(Order.due_date.asc().nullslast(), Order.created_at.desc())
    ).all()

    columns = {status_value.value: [] for status_value in OrderStatus if status_value
               != OrderStatus.cancelled}
    for order in rows:
        columns[order.status.value].append(_serialize(order))

    return {
        "columns": columns,
        "totals": {key: len(value) for key, value in columns.items()},
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderIn, context: ProductionContext, db: DbSession) -> dict:
    order = Order(
        organization_id=context.org_id,
        customer_id=payload.customer_id,
        number=_next_number(db, context.org_id),
        due_date=payload.due_date,
        rush=payload.rush,
        notes=payload.notes,
        placement=payload.placement,
        assigned_machine_id=payload.assigned_machine_id,
        discount=payload.discount,
        tax=payload.tax,
    )
    db.add(order)
    db.flush()

    for item in payload.items:
        _validate_refs(db, context, item.design_id, item.product_id)
        db.add(OrderItem(order_id=order.id, **item.model_dump()))
    db.flush()
    db.refresh(order)

    _recalculate(order)
    # Append through the relationship so the loaded collection stays in sync;
    # a bare db.add() leaves the already-loaded order.events stale.
    order.events.append(OrderEvent(
        order_id=order.id, user_id=context.user_id,
        from_status=None, to_status=order.status.value, note="Order created.",
    ))
    db.flush()
    return _serialize(_get(db, context, order.id))


@router.get("/{order_id}")
def get_order(order_id: uuid.UUID, context: ProductionContext, db: DbSession) -> dict:
    return _serialize(_get(db, context, order_id))


@router.put("/{order_id}")
def update_order(
    order_id: uuid.UUID, payload: OrderIn, context: ProductionContext, db: DbSession
) -> dict:
    order = _get(db, context, order_id)
    data = payload.model_dump(exclude={"items"})
    for field, value in data.items():
        setattr(order, field, value)

    order.items.clear()
    db.flush()
    for item in payload.items:
        _validate_refs(db, context, item.design_id, item.product_id)
        order.items.append(OrderItem(order_id=order.id, **item.model_dump()))
    db.flush()
    _recalculate(order)
    db.flush()
    return _serialize(_get(db, context, order.id))


@router.post("/{order_id}/status")
def change_status(
    order_id: uuid.UUID,
    payload: OrderStatusUpdate,
    context: ProductionContext,
    db: DbSession,
) -> dict:
    order = _get(db, context, order_id)
    target = OrderStatus(payload.status)

    if target == order.status:
        return _serialize(order)
    if target not in TRANSITIONS[order.status]:
        allowed = ", ".join(sorted(s.value for s in TRANSITIONS[order.status])) or "nothing"
        raise HTTPException(
            status_code=409,
            detail=f"An order in '{order.status.value}' can only move to: {allowed}.",
        )

    # Moving into sewing consumes stock; that is the point at which blanks
    # physically leave the shelf.
    if target == OrderStatus.sewing:
        _consume_stock(db, order)

    previous = order.status
    order.status = target
    order.events.append(OrderEvent(
        order_id=order.id, user_id=context.user_id,
        from_status=previous.value, to_status=target.value, note=payload.note,
    ))
    db.flush()
    return _serialize(_get(db, context, order.id))


@router.delete("/{order_id}", response_model=Message)
def delete_order(order_id: uuid.UUID, context: ProductionContext, db: DbSession) -> Message:
    db.delete(_get(db, context, order_id))
    return Message(detail="Order deleted.")


def _validate_refs(
    db: DbSession, context: CurrentContext,
    design_id: uuid.UUID | None, product_id: uuid.UUID | None,
) -> None:
    if design_id:
        design = db.get(Design, design_id)
        if design is None or design.organization_id != context.org_id:
            raise HTTPException(status_code=404, detail="Design not found.")
    if product_id:
        product = db.get(Product, product_id)
        if product is None or product.organization_id != context.org_id:
            raise HTTPException(status_code=404, detail="Product not found.")


def _consume_stock(db: DbSession, order: Order) -> None:
    for item in order.items:
        if item.product_id is None:
            continue
        product = db.get(Product, item.product_id)
        if product is None:
            continue
        if product.stock_quantity < item.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Not enough stock for {product.sku}: {product.stock_quantity} on hand, "
                       f"{item.quantity} needed.",
            )
        product.stock_quantity -= item.quantity
