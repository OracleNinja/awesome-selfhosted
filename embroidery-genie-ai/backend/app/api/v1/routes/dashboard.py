"""Dashboard aggregates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.deps import CurrentContext, DbSession
from app.models import (
    Customer,
    Design,
    DesignStatus,
    Invoice,
    InvoiceStatus,
    Order,
    OrderStatus,
    Product,
)
from app.services import plans

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(context: CurrentContext, db: DbSession) -> dict:
    org = context.org_id
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    design_total = db.scalar(
        select(func.count()).select_from(Design).where(Design.organization_id == org)
    ) or 0
    designs_ready = db.scalar(
        select(func.count()).select_from(Design).where(
            Design.organization_id == org, Design.status == DesignStatus.ready
        )
    ) or 0
    stitches_total = db.scalar(
        select(func.coalesce(func.sum(Design.stitch_count), 0)).where(
            Design.organization_id == org
        )
    ) or 0
    designs_this_month = db.scalar(
        select(func.count()).select_from(Design).where(
            Design.organization_id == org, Design.created_at >= month_start
        )
    ) or 0

    recent_designs = db.scalars(
        select(Design)
        .where(Design.organization_id == org)
        .order_by(Design.created_at.desc())
        .limit(6)
    ).all()

    orders_by_status = {
        row.status.value: row.count
        for row in db.execute(
            select(Order.status, func.count(Order.id).label("count"))
            .where(Order.organization_id == org)
            .group_by(Order.status)
        ).all()
    }

    open_orders = sum(
        count for status_key, count in orders_by_status.items()
        if status_key not in ("delivered", "cancelled")
    )

    revenue_month = db.scalar(
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.organization_id == org,
            Order.created_at >= month_start,
            Order.status != OrderStatus.cancelled,
        )
    ) or 0
    cost_month = db.scalar(
        select(func.coalesce(func.sum(Order.cost_total), 0)).where(
            Order.organization_id == org,
            Order.created_at >= month_start,
            Order.status != OrderStatus.cancelled,
        )
    ) or 0

    overdue_orders = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.organization_id == org,
            Order.due_date < date.today(),
            Order.status.notin_([OrderStatus.delivered, OrderStatus.cancelled]),
        )
    ) or 0

    due_soon = db.scalars(
        select(Order)
        .where(
            Order.organization_id == org,
            Order.due_date.is_not(None),
            Order.due_date <= date.today() + timedelta(days=7),
            Order.status.notin_([OrderStatus.delivered, OrderStatus.cancelled]),
        )
        .order_by(Order.due_date.asc())
        .limit(8)
    ).all()

    outstanding = db.scalar(
        select(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0)).where(
            Invoice.organization_id == org,
            Invoice.status.notin_([InvoiceStatus.paid, InvoiceStatus.void]),
        )
    ) or 0

    customers = db.scalar(
        select(func.count()).select_from(Customer).where(Customer.organization_id == org)
    ) or 0

    low_stock = db.scalar(
        select(func.count()).select_from(Product).where(
            Product.organization_id == org,
            Product.active.is_(True),
            Product.stock_quantity <= Product.reorder_level,
        )
    ) or 0

    revenue = float(revenue_month)
    cost = float(cost_month)

    return {
        "designs": {
            "total": design_total,
            "ready": designs_ready,
            "this_month": designs_this_month,
            "total_stitches": int(stitches_total),
            "recent": [
                {
                    "id": str(d.id),
                    "name": d.name,
                    "status": d.status.value,
                    "stitch_count": d.stitch_count,
                    "color_count": d.color_count,
                    "compatibility_score": d.compatibility_score,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in recent_designs
            ],
        },
        "orders": {
            "by_status": orders_by_status,
            "open": open_orders,
            "overdue": overdue_orders,
            "due_soon": [
                {
                    "id": str(o.id),
                    "number": o.number,
                    "status": o.status.value,
                    "due_date": o.due_date.isoformat() if o.due_date else None,
                    "total": float(o.total or 0),
                    "customer": o.customer.name if o.customer else None,
                }
                for o in due_soon
            ],
        },
        "finance": {
            "revenue_month": round(revenue, 2),
            "cost_month": round(cost, 2),
            "profit_month": round(revenue - cost, 2),
            "margin_pct": round((revenue - cost) / revenue * 100, 1) if revenue > 0 else 0.0,
            "outstanding_invoices": round(float(outstanding), 2),
            "currency": context.organization.currency,
        },
        "customers": {"total": customers},
        "inventory": {"low_stock": low_stock},
        "subscription": plans.quota_status(db, org),
    }
