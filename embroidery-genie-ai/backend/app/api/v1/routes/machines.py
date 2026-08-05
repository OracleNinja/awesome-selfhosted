"""Module 5 — Machine profiles."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentContext, DbSession
from app.models import Machine
from app.schemas import MachineIn, MachineOut, Message
from app.services.machines import get_preset, list_presets

router = APIRouter(prefix="/machines", tags=["machines"])


@router.get("", response_model=list[MachineOut])
def list_machines(context: CurrentContext, db: DbSession) -> list[Machine]:
    return list(
        db.scalars(
            select(Machine)
            .where(Machine.organization_id == context.org_id)
            .order_by(Machine.is_default.desc(), Machine.name)
        ).all()
    )


@router.get("/presets")
def presets() -> dict:
    return {"presets": list_presets()}


@router.post("", response_model=MachineOut, status_code=status.HTTP_201_CREATED)
def create_machine(payload: MachineIn, context: CurrentContext, db: DbSession) -> Machine:
    machine = Machine(organization_id=context.org_id, **payload.model_dump())
    if machine.is_default:
        _clear_defaults(db, context.org_id)
    db.add(machine)
    db.flush()
    return machine


@router.post("/from-preset/{preset_key}", response_model=MachineOut,
             status_code=status.HTTP_201_CREATED)
def create_from_preset(preset_key: str, context: CurrentContext, db: DbSession) -> Machine:
    preset = get_preset(preset_key)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset '{preset_key}'.")

    existing = db.scalar(
        select(Machine).where(Machine.organization_id == context.org_id).limit(1)
    )
    machine = Machine(
        organization_id=context.org_id,
        name=f"{preset.brand} {preset.model}",
        brand=preset.brand,
        model=preset.model,
        heads=preset.heads,
        needle_count=preset.needle_count,
        hoop_width_mm=preset.hoop_width_mm,
        hoop_height_mm=preset.hoop_height_mm,
        max_stitch_count=preset.max_stitch_count,
        max_speed_spm=preset.max_speed_spm,
        supported_formats=list(preset.formats),
        notes=preset.notes,
        is_default=existing is None,
    )
    db.add(machine)
    db.flush()
    return machine


@router.get("/{machine_id}", response_model=MachineOut)
def get_machine(machine_id: uuid.UUID, context: CurrentContext, db: DbSession) -> Machine:
    return _get(db, context, machine_id)


@router.put("/{machine_id}", response_model=MachineOut)
def update_machine(
    machine_id: uuid.UUID, payload: MachineIn, context: CurrentContext, db: DbSession
) -> Machine:
    machine = _get(db, context, machine_id)
    data = payload.model_dump()
    if data.get("is_default"):
        _clear_defaults(db, context.org_id)
    for field, value in data.items():
        setattr(machine, field, value)
    db.flush()
    return machine


@router.delete("/{machine_id}", response_model=Message)
def delete_machine(machine_id: uuid.UUID, context: CurrentContext, db: DbSession) -> Message:
    db.delete(_get(db, context, machine_id))
    return Message(detail="Machine removed.")


def _get(db: DbSession, context: CurrentContext, machine_id: uuid.UUID) -> Machine:
    machine = db.scalar(
        select(Machine).where(
            Machine.id == machine_id, Machine.organization_id == context.org_id
        )
    )
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found.")
    return machine


def _clear_defaults(db: DbSession, org_id: uuid.UUID) -> None:
    for machine in db.scalars(
        select(Machine).where(Machine.organization_id == org_id, Machine.is_default.is_(True))
    ).all():
        machine.is_default = False
