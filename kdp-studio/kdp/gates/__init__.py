"""The quality-control gates.

Each gate is a module exposing ``GATE_ID``, ``STAGE`` and ``run(...)``. They
are deliberately not a plugin system: the set of gates a book must clear is
fixed and reviewable, and adding one should be a visible change here.
"""

from __future__ import annotations

from . import (
    metadata_compliance,
    originality,
    print_preflight,
    provenance_complete,
    research_hygiene,
    spec_legality,
)
from .base import Finding, GateResult, Severity, Status, decide
from .runner import StageReport, run_stage

#: Which gates guard which stage. The pipeline reads this to decide what to
#: run before allowing an advance.
GATES_BY_STAGE: dict[str, tuple[str, ...]] = {
    "research": (research_hygiene.GATE_ID,),
    "concept": (spec_legality.GATE_ID,),
    "generation": (provenance_complete.GATE_ID,),
    "qa": (originality.GATE_ID,),
    "production": (print_preflight.GATE_ID,),
    "publishing_prep": (metadata_compliance.GATE_ID,),
}

__all__ = [
    "GATES_BY_STAGE",
    "Finding",
    "GateResult",
    "Severity",
    "StageReport",
    "Status",
    "decide",
    "metadata_compliance",
    "originality",
    "print_preflight",
    "provenance_complete",
    "research_hygiene",
    "run_stage",
    "spec_legality",
]
