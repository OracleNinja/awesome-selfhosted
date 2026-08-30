"""The quality-control gates.

Each gate is a module exposing ``GATE_ID``, ``STAGE``, ``VALIDATOR_VERSION``
and ``run(...)``. They are deliberately not a plugin system: the set of gates a
book must clear is fixed and reviewable, and adding one should be a visible
change here.

Gate ids are stable and are never renumbered. G1-G6 kept their ids when the
pipeline grew from six stages to eleven, because a stored gate report from an
older run must still say what it said. New gates take the next free number.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import (
    asset_qa,
    cover_qa,
    final_audit,
    human_approval,
    interior_qa,
    kdp_compliance,
    metadata_compliance,
    originality,
    print_preflight,
    prompt_plan,
    provenance_complete,
    research_hygiene,
    spec_legality,
    strategy_differentiation,
)
from .base import Finding, GateResult, Severity, Status, decide
from .runner import StageReport, run_stage


@dataclass(frozen=True, slots=True)
class GateSpec:
    """What a gate is, for documentation and for the CLI's gate listing."""

    gate_id: str
    stage: str
    purpose: str
    #: What the gate needs in order to be evaluated at all. Absent inputs
    #: produce BLOCKED, never PASS.
    requires: tuple[str, ...]
    validator_version: str


#: Every gate, in pipeline order. The single source of truth for which gates
#: guard which stage.
GATES: tuple[GateSpec, ...] = (
    GateSpec(
        research_hygiene.GATE_ID,
        "research",
        "Research stayed research: abstracted signals, no competitor expression.",
        ("research brief",),
        "1",
    ),
    GateSpec(
        strategy_differentiation.GATE_ID,
        "strategy",
        "The book is differentiated by decisions, not adjectives.",
        ("strategy",),
        strategy_differentiation.VALIDATOR_VERSION,
    ),
    GateSpec(
        spec_legality.GATE_ID,
        "concept",
        "The specification is printable and internally consistent.",
        ("book spec", "verified print tables"),
        "1",
    ),
    GateSpec(
        prompt_plan.GATE_ID,
        "planning",
        "Every art page has its own sufficient, distinct prompt.",
        ("book spec", "prompt plan"),
        prompt_plan.VALIDATOR_VERSION,
    ),
    GateSpec(
        provenance_complete.GATE_ID,
        "generation",
        "Every shipping asset is accounted for and classified.",
        ("assets with provenance",),
        "1",
    ),
    GateSpec(
        originality.GATE_ID,
        "qa",
        "No repeated pages within the book or across the catalogue.",
        ("assets", "catalogue hashes", "Pillow for near-duplicates"),
        "1",
    ),
    GateSpec(
        asset_qa.GATE_ID,
        "qa",
        "Each shipping asset meets line-art and print requirements.",
        ("assets", "Pillow for pixel inspection"),
        asset_qa.VALIDATOR_VERSION,
    ),
    GateSpec(
        print_preflight.GATE_ID,
        "production",
        "Print geometry is resolvable and the build inputs exist.",
        ("book spec", "interior PDF", "cover PDF", "pypdf"),
        "1",
    ),
    GateSpec(
        interior_qa.GATE_ID,
        "production_qa",
        "The interior PDF on disk is the one that was built and specified.",
        ("interior PDF artefact", "pypdf"),
        interior_qa.VALIDATOR_VERSION,
    ),
    GateSpec(
        cover_qa.GATE_ID,
        "production_qa",
        "The cover matches the current page count and is not stale.",
        ("cover PDF artefact", "pypdf"),
        cover_qa.VALIDATOR_VERSION,
    ),
    GateSpec(
        metadata_compliance.GATE_ID,
        "publishing_prep",
        "The listing is well formed and the disclosure is confirmed.",
        ("metadata", "provenance"),
        "1",
    ),
    GateSpec(
        kdp_compliance.GATE_ID,
        "publishing_prep",
        "The listing would not draw a policy objection; risks flagged for review.",
        ("metadata", "verified policy table"),
        kdp_compliance.VALIDATOR_VERSION,
    ),
    GateSpec(
        final_audit.GATE_ID,
        "final_audit",
        "Could a human reasonably submit this book? Re-derived, not trusted.",
        ("everything above", "artefacts on disk"),
        final_audit.VALIDATOR_VERSION,
    ),
    GateSpec(
        human_approval.GATE_ID,
        "human_approval",
        "A person approved this exact book. No automation can satisfy it.",
        ("approval record matching the current fingerprint",),
        human_approval.VALIDATOR_VERSION,
    ),
)

#: Which gates guard which stage. Derived from GATES so the two cannot drift.
GATES_BY_STAGE: dict[str, tuple[str, ...]] = {}
for _spec in GATES:
    GATES_BY_STAGE.setdefault(_spec.stage, ())
    GATES_BY_STAGE[_spec.stage] += (_spec.gate_id,)
del _spec


def gate_spec(gate_id: str) -> GateSpec | None:
    for spec in GATES:
        if spec.gate_id == gate_id:
            return spec
    return None


__all__ = [
    "GATES",
    "GATES_BY_STAGE",
    "Finding",
    "GateResult",
    "GateSpec",
    "Severity",
    "StageReport",
    "Status",
    "asset_qa",
    "cover_qa",
    "decide",
    "final_audit",
    "gate_spec",
    "human_approval",
    "interior_qa",
    "kdp_compliance",
    "metadata_compliance",
    "originality",
    "print_preflight",
    "prompt_plan",
    "provenance_complete",
    "research_hygiene",
    "run_stage",
    "spec_legality",
    "strategy_differentiation",
]
