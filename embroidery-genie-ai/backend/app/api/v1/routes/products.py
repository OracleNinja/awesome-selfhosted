"""Module 8 — Inventory / blanks."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentContext, DbSession, ProductionContext
from app.models import Product
from app.schemas import Message, ProductIn, ProductOut, StockAdjust

router = APIRouter(prefix="/products", tags=["inventory"])


@router.get("")
def list_products(
    context: ProductionContext,
    db: DbSession,
    q: str | None = Query(None, max_length=120),
    low_stock: bool = False,
    active_only: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    stmt = select(Product).where(Product.organization_id == context.org_id)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern)))
    if active_only:
        stmt = stmt.where(Product.active.is_(True))
    if low_stock:
        stmt = stmt.where(Product.stock_quantity <= Product.reorder_level)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Product.name).limit(limit).offset(offset)).all()

    inventory_value = db.scalar(
        select(func.coalesce(func.sum(Product.blank_cost * Product.stock_quantity), 0)).where(
            Product.organization_id == context.org_id, Product.active.is_(True)
        )
    )

    return {
        "items": [
            {
                **ProductOut.model_validate(p).model_dump(mode="json"),
                "needs_reorder": p.stock_quantity <= p.reorder_level,
                "stock_value": round(float(p.blank_cost or 0) * p.stock_quantity, 2),
            }
            for p in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "inventory_value": round(float(inventory_value or 0), 2),
    }


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductIn, context: ProductionContext, db: DbSession) -> Product:
    product = Product(organization_id=context.org_id, **payload.model_dump())
    db.add(product)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"SKU '{payload.sku}' already exists in this workspace."
        ) from exc
    return product


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: uuid.UUID, context: ProductionContext, db: DbSession) -> Product:
    return _get(db, context, product_id)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: uuid.UUID, payload: ProductIn, context: ProductionContext, db: DbSession
) -> Product:
    product = _get(db, context, product_id)
    for field, value in payload.model_dump().items():
        setattr(product, field, value)
    db.flush()
    return product


@router.post("/{product_id}/stock", response_model=ProductOut)
def adjust_stock(
    product_id: uuid.UUID, payload: StockAdjust, context: ProductionContext, db: DbSession
) -> Product:
    """Receive or write off stock. Negative deltas cannot go below zero."""
    product = _get(db, context, product_id)
    updated = product.stock_quantity + payload.delta
    if updated < 0:
        raise HTTPException(
            status_code=409,
            detail=f"Only {product.stock_quantity} on hand; cannot remove {abs(payload.delta)}.",
        )
    product.stock_quantity = updated
    db.flush()
    return product


@router.delete("/{product_id}", response_model=Message)
def delete_product(product_id: uuid.UUID, context: ProductionContext, db: DbSession) -> Message:
    product = _get(db, context, product_id)
    # Soft delete: order history references this row.
    product.active = False
    db.flush()
    return Message(detail="Product archived.")


def _get(db: DbSession, context: CurrentContext, product_id: uuid.UUID) -> Product:
    product = db.scalar(
        select(Product).where(
            Product.id == product_id, Product.organization_id == context.org_id
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product
