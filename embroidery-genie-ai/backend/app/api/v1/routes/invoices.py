"""Module 8 — Invoicing."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import DbSession, ProductionContext
from app.models import Invoice, InvoiceStatus, Order, OrderItem
from app.schemas import InvoiceIn, InvoicePayment, Message

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _next_number(db: DbSession, org_id: uuid.UUID) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"INV-{year}-"
    last = db.scalar(
        select(func.max(Invoice.number)).where(
            Invoice.organization_id == org_id, Invoice.number.like(f"{prefix}%")
        )
    )
    sequence = 1
    if last:
        try:
            sequence = int(str(last).rsplit("-", 1)[-1]) + 1
        except ValueError:
            sequence = 1
    return f"{prefix}{sequence:04d}"


def _serialize(invoice: Invoice) -> dict:
    return {
        "id": str(invoice.id),
        "number": invoice.number,
        "status": invoice.status.value,
        "order_id": str(invoice.order_id) if invoice.order_id else None,
        "order_number": invoice.order.number if invoice.order else None,
        "customer_id": str(invoice.customer_id) if invoice.customer_id else None,
        "customer_name": invoice.customer.name if invoice.customer else None,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "subtotal": float(invoice.subtotal or 0),
        "tax": float(invoice.tax or 0),
        "total": float(invoice.total or 0),
        "amount_paid": float(invoice.amount_paid or 0),
        "balance_due": round(invoice.balance_due, 2),
        "currency": invoice.currency,
        "notes": invoice.notes,
        "line_items": invoice.line_items or [],
        "overdue": bool(
            invoice.due_date
            and invoice.due_date < date.today()
            and invoice.status not in (InvoiceStatus.paid, InvoiceStatus.void)
        ),
    }


def _get(db: DbSession, context: ProductionContext, invoice_id: uuid.UUID) -> Invoice:
    invoice = db.scalar(
        select(Invoice)
        .options(selectinload(Invoice.order), selectinload(Invoice.customer))
        .where(Invoice.id == invoice_id, Invoice.organization_id == context.org_id)
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return invoice


@router.get("")
def list_invoices(
    context: ProductionContext,
    db: DbSession,
    status_filter: str | None = Query(None, alias="status"),
    customer_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    stmt = select(Invoice).where(Invoice.organization_id == context.org_id)
    if status_filter:
        try:
            stmt = stmt.where(Invoice.status == InvoiceStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown status.") from exc
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(selectinload(Invoice.order), selectinload(Invoice.customer))
        .order_by(Invoice.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    outstanding = db.scalar(
        select(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0)).where(
            Invoice.organization_id == context.org_id,
            Invoice.status.notin_([InvoiceStatus.paid, InvoiceStatus.void]),
        )
    )

    return {
        "items": [_serialize(i) for i in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "outstanding": round(float(outstanding or 0), 2),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvoiceIn, context: ProductionContext, db: DbSession) -> dict:
    line_items = list(payload.line_items)
    customer_id = payload.customer_id
    order = None

    if payload.order_id:
        order = db.scalar(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.design))
            .where(Order.id == payload.order_id, Order.organization_id == context.org_id)
        )
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found.")
        customer_id = customer_id or order.customer_id
        if not line_items:
            # Build the invoice straight off the order so nothing is retyped.
            line_items = [
                {
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price or 0),
                    "total": round(item.line_total, 2),
                }
                for item in order.items
            ]

    subtotal = sum(float(item.get("total", 0)) for item in line_items)
    invoice = Invoice(
        organization_id=context.org_id,
        order_id=payload.order_id,
        customer_id=customer_id,
        number=_next_number(db, context.org_id),
        issue_date=payload.issue_date or date.today(),
        due_date=payload.due_date or (date.today() + timedelta(days=30)),
        subtotal=round(subtotal, 2),
        tax=payload.tax,
        total=round(subtotal + float(payload.tax or 0), 2),
        currency=context.organization.currency,
        notes=payload.notes,
        line_items=line_items,
    )
    db.add(invoice)
    db.flush()
    return _serialize(_get(db, context, invoice.id))


@router.get("/{invoice_id}")
def get_invoice(invoice_id: uuid.UUID, context: ProductionContext, db: DbSession) -> dict:
    return _serialize(_get(db, context, invoice_id))


@router.post("/{invoice_id}/send")
def send_invoice(invoice_id: uuid.UUID, context: ProductionContext, db: DbSession) -> dict:
    invoice = _get(db, context, invoice_id)
    if invoice.status != InvoiceStatus.draft:
        raise HTTPException(
            status_code=409, detail=f"Invoice is already {invoice.status.value}."
        )
    invoice.status = InvoiceStatus.sent
    db.flush()
    return _serialize(invoice)


@router.post("/{invoice_id}/payments")
def record_payment(
    invoice_id: uuid.UUID,
    payload: InvoicePayment,
    context: ProductionContext,
    db: DbSession,
) -> dict:
    invoice = _get(db, context, invoice_id)
    if invoice.status == InvoiceStatus.void:
        raise HTTPException(status_code=409, detail="This invoice has been voided.")

    remaining = invoice.balance_due
    if payload.amount > remaining + 0.005:
        raise HTTPException(
            status_code=409,
            detail=f"Payment of {payload.amount:.2f} exceeds the balance due "
                   f"of {remaining:.2f}.",
        )

    invoice.amount_paid = round(float(invoice.amount_paid or 0) + payload.amount, 2)
    if invoice.balance_due <= 0.005:
        invoice.status = InvoiceStatus.paid
    db.flush()
    return _serialize(invoice)


@router.post("/{invoice_id}/void", response_model=Message)
def void_invoice(invoice_id: uuid.UUID, context: ProductionContext, db: DbSession) -> Message:
    invoice = _get(db, context, invoice_id)
    if invoice.status == InvoiceStatus.paid:
        raise HTTPException(
            status_code=409, detail="A paid invoice cannot be voided; issue a credit instead."
        )
    invoice.status = InvoiceStatus.void
    db.flush()
    return Message(detail="Invoice voided.")
