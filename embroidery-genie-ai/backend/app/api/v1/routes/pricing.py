"""Module 9 — AI Pricing Assistant."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.deps import CurrentContext, DbSession
from app.embroidery.fabrics import list_fabrics
from app.models import Design, Machine
from app.schemas import PricingRequest
from app.services.pricing import PricingInputs, calculate

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.post("/quote")
def quote(payload: PricingRequest, context: CurrentContext, db: DbSession) -> dict:
    """Price a job.

    Pass a ``design_id`` to pull the real stitch count and colour count, or
    pass the numbers directly for a what-if.
    """
    stitch_count = payload.stitch_count
    color_count = payload.color_count
    trim_count = payload.trim_count
    thread_length_m = None
    fabric = payload.fabric
    design = None

    if payload.design_id:
        design = db.scalar(
            select(Design).where(
                Design.id == payload.design_id, Design.organization_id == context.org_id
            )
        )
        if design is None:
            raise HTTPException(status_code=404, detail="Design not found.")
        if design.stitch_count is None:
            raise HTTPException(
                status_code=409,
                detail="This design has not been digitized yet, so there is no stitch "
                       "count to price. Digitize it first.",
            )
        stitch_count = design.stitch_count
        color_count = design.color_count or color_count
        trim_count = design.trim_count or 0
        thread_length_m = design.thread_length_m
        if design.settings:
            fabric = design.settings.fabric

    if stitch_count is None:
        raise HTTPException(
            status_code=422,
            detail="Provide either a design_id or a stitch_count.",
        )

    speed = payload.machine_speed_spm
    heads = payload.heads
    machine_rate = payload.machine_rate_per_hour
    if payload.machine_id:
        machine = db.scalar(
            select(Machine).where(
                Machine.id == payload.machine_id, Machine.organization_id == context.org_id
            )
        )
        if machine is None:
            raise HTTPException(status_code=404, detail="Machine not found.")
        speed = machine.max_speed_spm
        heads = machine.heads
        machine_rate = machine_rate or machine.hourly_rate

    result = calculate(
        PricingInputs(
            stitch_count=stitch_count,
            color_count=color_count,
            trim_count=trim_count,
            quantity=payload.quantity,
            blank_cost=payload.blank_cost,
            fabric=fabric,
            machine_speed_spm=speed,
            heads=heads,
            thread_length_m=thread_length_m,
            digitizing_already_paid=payload.digitizing_already_paid,
            wholesale_margin=payload.wholesale_margin,
            retail_margin=payload.retail_margin,
            rush=payload.rush,
            currency=context.organization.currency,
            **{
                key: value
                for key, value in {
                    "labor_rate_per_hour": payload.labor_rate_per_hour,
                    "machine_rate_per_hour": machine_rate,
                    "digitizing_fee": payload.digitizing_fee,
                }.items()
                if value is not None
            },
        )
    )

    return {
        **result.to_dict(),
        "design": {
            "id": str(design.id), "name": design.name,
            "stitch_count": design.stitch_count, "color_count": design.color_count,
        } if design else None,
        "inputs": {
            "stitch_count": stitch_count,
            "color_count": color_count,
            "quantity": payload.quantity,
            "fabric": fabric,
            "machine_speed_spm": speed,
            "heads": heads,
        },
    }


@router.get("/defaults")
def defaults(context: CurrentContext) -> dict:
    from app.core.config import settings

    return {
        "currency": context.organization.currency,
        "labor_rate_per_hour": settings.default_labor_rate_per_hour,
        "machine_rate_per_hour": settings.default_machine_rate_per_hour,
        "digitizing_fee": settings.default_digitizing_fee,
        "wholesale_margin": settings.default_wholesale_margin,
        "retail_margin": settings.default_retail_margin,
        "fabrics": [
            {"key": f["key"], "name": f["name"]} for f in list_fabrics()
        ],
    }
