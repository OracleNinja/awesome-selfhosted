"""G1 — research hygiene.

Checks that the research corpus stayed research: abstracted market signals in
our own words, with no competitor creative expression carried across.

``MarketSignal`` already blocks the structural routes in (unknown fields are
rejected at construction). This gate catches what the type system cannot: an
observation long enough to hide copied text, a file reference smuggled into a
string field, or an observation that reads as a verbatim quotation.
"""

from __future__ import annotations

from ..models.research import (
    FORBIDDEN_REFERENCE_MARKERS,
    MAX_OBSERVATION_CHARS,
    ResearchBrief,
)
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G1.research_hygiene"
STAGE = "research"

#: Characters that open a quotation. An observation that starts with one is
#: very likely carrying someone else's words rather than our summary of them.
_QUOTE_OPENERS = ('"', "'", "“", "‘", "«")


def run(brief: ResearchBrief | None) -> GateResult:
    if brief is None:
        return decide(
            GATE_ID,
            STAGE,
            [],
            blocked_reason=(
                "No research brief attached. Research hygiene cannot be "
                "certified over a corpus that does not exist."
            ),
        )

    findings: list[Finding] = []

    if not brief.signals:
        findings.append(
            Finding(
                code="research.no_signals",
                severity=Severity.BLOCKER,
                message="Research brief carries no market signals.",
                subject=brief.brief_id,
            )
        )

    if not brief.opportunity.strip():
        findings.append(
            Finding(
                code="research.no_opportunity",
                severity=Severity.MAJOR,
                message=(
                    "Research brief names no opportunity. A book with no stated "
                    "gap to fill is the kind of undifferentiated title KDP "
                    "removes as duplicative."
                ),
                subject=brief.brief_id,
            )
        )

    for signal in brief.signals:
        for position, observation in enumerate(signal.observations):
            subject = f"{signal.signal_id}[{position}]"

            if len(observation) > MAX_OBSERVATION_CHARS:
                findings.append(
                    Finding(
                        code="research.observation_too_long",
                        severity=Severity.BLOCKER,
                        message=(
                            f"Observation is {len(observation)} characters; the "
                            f"cap is {MAX_OBSERVATION_CHARS}. Long free text in "
                            "a research record is where copied material hides. "
                            "Summarise it instead."
                        ),
                        subject=subject,
                        detail={"length": len(observation)},
                    )
                )

            lowered = observation.lower()
            hits = [m for m in FORBIDDEN_REFERENCE_MARKERS if m in lowered]
            if hits:
                findings.append(
                    Finding(
                        code="research.asset_reference",
                        severity=Severity.BLOCKER,
                        message=(
                            "Observation references a media file. The corpus "
                            "stores abstracted signals, never pointers to "
                            "competitor artwork."
                        ),
                        subject=subject,
                        detail={"markers": hits},
                    )
                )

            stripped = observation.strip()
            if stripped.startswith(_QUOTE_OPENERS):
                findings.append(
                    Finding(
                        code="research.possible_verbatim",
                        severity=Severity.BLOCKER,
                        message=(
                            "Observation opens with a quotation mark, which "
                            "reads as copied text. Paraphrase it in our own "
                            "words."
                        ),
                        subject=subject,
                    )
                )

    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "brief_id": brief.brief_id,
            "signal_count": len(brief.signals),
            "observation_count": sum(len(s.observations) for s in brief.signals),
        },
    )
