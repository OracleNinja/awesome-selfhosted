"""G12 — KDP compliance.

The policy-facing review, separate from G6's structural metadata checks. G6
asks "is this listing well formed?"; this asks "would Amazon object to it?"

Two things it deliberately does not do:

**It does not decide copyright questions.** It flags proximity — a metadata
string that reads like a trademark, a subject that names a likely-protected
character — and routes it to a human. A similarity score is a QA signal, never
a legal determination, and this gate never claims otherwise.

**It does not trust stale policy.** If the ``policy`` specification table is
unverified or aged out, the gate BLOCKS. Certifying compliance against rules
nobody checked is the failure mode this whole system exists to avoid.
"""

from __future__ import annotations

import re

from ..models.manifest import BookManifest
from ..specs.revision import source, verification_problem
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G12.kdp_compliance"
STAGE = "publishing_prep"
VALIDATOR_VERSION = "1"

#: Phrases that are unsupportable in a listing unless independently true.
_UNSUPPORTED_CLAIM_PATTERNS = (
    (r"\bbest[\s-]?sell(er|ing)\b", "best-seller claim"),
    (r"\b#\s?1\b", "number-one ranking claim"),
    (r"\baward[\s-]?winning\b", "award claim"),
    (r"\bofficial(ly)?\s+licen[cs]ed\b", "licensing claim"),
    (r"\bas\s+seen\s+on\b", "media endorsement claim"),
    (r"\bguaranteed\b", "guarantee"),
)

#: Signals that a string may name someone else's property. Deliberately a
#: prompt for review rather than a verdict.
_TRADEMARK_HINTS = (
    "disney",
    "pixar",
    "marvel",
    "pokemon",
    "pokémon",
    "harry potter",
    "star wars",
    "bluey",
    "peppa pig",
    "paw patrol",
    "hello kitty",
    "minecraft",
    "roblox",
    "barbie",
    "lego",
)


def _scan(text: str, subject: str) -> list[Finding]:
    findings: list[Finding] = []
    lowered = text.lower()

    for pattern, label in _UNSUPPORTED_CLAIM_PATTERNS:
        if re.search(pattern, lowered):
            findings.append(
                Finding(
                    code="compliance.unsupported_claim",
                    severity=Severity.BLOCKER,
                    message=(
                        f"Contains a {label}. Listing copy may not make claims "
                        "the book cannot support."
                    ),
                    subject=subject,
                    detail={"pattern": label},
                )
            )

    for hint in _TRADEMARK_HINTS:
        if hint in lowered:
            findings.append(
                Finding(
                    code="compliance.trademark_risk",
                    severity=Severity.BLOCKER,
                    message=(
                        f"Mentions {hint!r}, which is likely a third-party "
                        "trademark. This is a flag for human review, not a legal "
                        "determination: either remove it or have someone confirm "
                        "the use is lawful."
                    ),
                    subject=subject,
                    detail={"term": hint},
                )
            )
    return findings


def run(manifest: BookManifest, *, require_verified_policy: bool = True) -> GateResult:
    findings: list[Finding] = []
    evidence: dict = {
        "validator_version": VALIDATOR_VERSION,
        "book_id": manifest.book_id,
        "policy_source": source("policy").to_dict(),
    }

    if require_verified_policy:
        problem = verification_problem("policy")
        if problem:
            return decide(
                GATE_ID,
                STAGE,
                [],
                evidence=evidence,
                blocked_reason=(
                    f"{problem} Compliance cannot be certified against policy "
                    "data that has not been checked."
                ),
            )

    metadata = manifest.metadata
    if metadata is None:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason="No metadata attached; there is no listing to review.",
        )

    findings += _scan(metadata.title, "title")
    findings += _scan(metadata.subtitle, "subtitle")
    findings += _scan(metadata.description, "description")
    for keyword in metadata.keywords:
        findings += _scan(keyword, "keywords")
    for page in manifest.spec.pages:
        findings += _scan(page.subject, page.page_id)

    disclosure = manifest.disclosure()
    evidence["disclosure"] = disclosure.to_dict()
    if not disclosure.complete:
        findings.append(
            Finding(
                code="compliance.disclosure_incomplete",
                severity=Severity.BLOCKER,
                message=(
                    "The AI-content disclosure cannot be determined: "
                    f"{len(disclosure.unclassified)} shipping asset(s) are "
                    "unclassified."
                ),
                subject="ai_disclosure",
            )
        )

    # A listing that describes a different book than the one built.
    art_pages = len(manifest.spec.art_pages)
    for number in re.findall(r"\b(\d{2,3})\s+(?:pages|designs|illustrations)\b",
                             metadata.description.lower()):
        claimed = int(number)
        if claimed > art_pages:
            findings.append(
                Finding(
                    code="compliance.metadata_overstates_content",
                    severity=Severity.BLOCKER,
                    message=(
                        f"Description advertises {claimed} but the book contains "
                        f"{art_pages} art pages."
                    ),
                    subject="description",
                    detail={"claimed": claimed, "actual": art_pages},
                )
            )

    if manifest.strategy is not None and manifest.strategy.risks:
        findings.append(
            Finding(
                code="compliance.strategy_risks_recorded",
                severity=Severity.INFO,
                message="Risks named at strategy: " + "; ".join(manifest.strategy.risks),
                subject="risks",
            )
        )

    return decide(GATE_ID, STAGE, findings, evidence=evidence)
