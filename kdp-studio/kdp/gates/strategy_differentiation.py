"""G7 — strategy differentiation.

Refuses a book that is a clone. The check is not "is this original art" — that
is G4's job on the finished pages — but "did anyone decide what makes this book
different before a single page was drawn?"

The distinction it enforces is between a *decision* and an *adjective*. "Higher
quality", "better designed" and "more appealing" are not differentiators; they
are hopes. "Single-sided pages", "difficulty rises across five bands", "subjects
drawn from northern-hemisphere winter flora" are decisions a reader could
verify. So vague differentiators are rejected by name.
"""

from __future__ import annotations

from ..models.strategy import BookStrategy, Confidence
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G7.strategy_differentiation"
STAGE = "strategy"
VALIDATOR_VERSION = "1"

#: A book needs at least this many concrete differentiators to be worth making.
MIN_DIFFERENTIATORS = 2

#: Words that describe a hope rather than a decision. A differentiator built
#: only from these is not a differentiator.
_VAGUE_TERMS = (
    "better",
    "high quality",
    "higher quality",
    "best",
    "premium",
    "improved",
    "nicer",
    "more appealing",
    "beautiful",
    "unique",
    "great",
    "superior",
)

MIN_ANGLE_CHARS = 40


def _is_vague(text: str) -> bool:
    lowered = text.lower().strip()
    if len(lowered) < 12:
        return True
    stripped = lowered
    for term in _VAGUE_TERMS:
        stripped = stripped.replace(term, "")
    # If removing the marketing words leaves almost nothing, that is all it was.
    return len(stripped.strip(" .,-—")) < 10


def run(strategy: BookStrategy | None) -> GateResult:
    if strategy is None:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence={"validator_version": VALIDATOR_VERSION},
            blocked_reason=(
                "No strategy attached to the manifest. A book cannot be shown "
                "to be differentiated before anyone has said how it differs."
            ),
        )

    findings: list[Finding] = []

    if len(strategy.unique_angle.strip()) < MIN_ANGLE_CHARS:
        findings.append(
            Finding(
                code="strategy.no_unique_angle",
                severity=Severity.BLOCKER,
                message=(
                    "Strategy states no unique angle. A book with no articulated "
                    "difference is the undifferentiated kind KDP removes."
                ),
                subject="unique_angle",
            )
        )

    concrete = [d for d in strategy.differentiators if not _is_vague(d)]
    vague = [d for d in strategy.differentiators if _is_vague(d)]

    for item in vague:
        findings.append(
            Finding(
                code="strategy.vague_differentiator",
                severity=Severity.MAJOR,
                message=(
                    f"{item!r} describes a hope, not a decision. Name something "
                    "a reader could verify by opening the book."
                ),
                subject="differentiators",
            )
        )

    if len(concrete) < MIN_DIFFERENTIATORS:
        findings.append(
            Finding(
                code="strategy.insufficient_differentiation",
                severity=Severity.BLOCKER,
                message=(
                    f"{len(concrete)} concrete differentiator(s); at least "
                    f"{MIN_DIFFERENTIATORS} are needed. Without them this is a "
                    "clone of what already sells."
                ),
                subject="differentiators",
                detail={"concrete": concrete, "vague": vague},
            )
        )

    if not strategy.audience.strip():
        findings.append(
            Finding(
                code="strategy.no_audience",
                severity=Severity.BLOCKER,
                message="Strategy names no audience.",
                subject="audience",
            )
        )

    if not strategy.purpose.strip():
        findings.append(
            Finding(
                code="strategy.no_purpose",
                severity=Severity.MAJOR,
                message="Strategy states no reader benefit.",
                subject="purpose",
            )
        )

    if not strategy.risks:
        findings.append(
            Finding(
                code="strategy.no_risks_considered",
                severity=Severity.MAJOR,
                message=(
                    "No risks named. Saturation, trademark proximity and "
                    "seasonality are worth stating even when the answer is "
                    "'low'."
                ),
                subject="risks",
            )
        )

    # Estimated figures presented without a confidence label are how a guess
    # becomes a fact three documents later.
    #
    # UNKNOWN is both the enum's default and a deliberate answer, so the two
    # are indistinguishable from the confidence alone. A stated basis separates
    # them: "we looked and there is no data" comes with a reason, a forgotten
    # label does not. Flagging both would fire on correct usage, and a warning
    # that cries wolf gets ignored — including on the run where it is right.
    unlabelled = [
        c.label
        for c in strategy.claims
        if c.confidence is Confidence.UNKNOWN and not c.basis.strip()
    ]
    if unlabelled:
        findings.append(
            Finding(
                code="strategy.unlabelled_claims",
                severity=Severity.MAJOR,
                message=(
                    "Market claims carry no confidence label: "
                    f"{', '.join(sorted(unlabelled))}. An unlabelled estimate "
                    "reads as an observation."
                ),
                subject="claims",
                detail={"claims": sorted(unlabelled)},
            )
        )

    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "validator_version": VALIDATOR_VERSION,
            "strategy_id": strategy.strategy_id,
            "concrete_differentiators": len(concrete),
            "risks": len(strategy.risks),
            "evidence_backed_claims": sum(
                1 for c in strategy.claims if c.confidence.is_evidence
            ),
        },
    )
