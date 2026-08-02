"""Module 11 — Voice command parsing."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentContext
from app.schemas import VoiceRequest
from app.services.voice import examples, parse

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/command")
def command(payload: VoiceRequest, context: CurrentContext) -> dict:
    """Turn a spoken phrase into a structured action for the client to run."""
    return parse(payload.transcript).to_dict()


@router.get("/examples")
def command_examples() -> dict:
    return {"examples": examples()}
