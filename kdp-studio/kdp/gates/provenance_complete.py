"""G3 — provenance completeness.

Every page the spec describes must have an asset with a provenance record, and
every record must be classified. This gate is what makes the disclosure answer
in G6 trustworthy: it is checked once, here, before QA spends any time on
artwork that may not be accounted for.
"""

from __future__ import annotations

from ..models.manifest import BookManifest
from ..models.provenance import AIRole
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G3.provenance_complete"
STAGE = "generation"


def run(manifest: BookManifest) -> GateResult:
    findings: list[Finding] = []

    described = {p.page_id for p in manifest.spec.pages if p.role == "art"}
    recorded = {a.asset_id for a in manifest.assets}

    for missing in sorted(described - recorded):
        findings.append(
            Finding(
                code="provenance.missing_record",
                severity=Severity.BLOCKER,
                message="Art page has no provenance record.",
                subject=missing,
            )
        )

    for orphan in sorted(recorded - described):
        record = manifest.asset(orphan)
        # Assets that are not art pages (cover, metadata copy) are legitimate;
        # only flag ones that claim to be pages the spec never mentioned.
        if record is not None and record.kind.value == "image":
            findings.append(
                Finding(
                    code="provenance.orphan_asset",
                    severity=Severity.MINOR,
                    message=(
                        "Image asset is not one of the spec's art pages. "
                        "Confirm it belongs in the book."
                    ),
                    subject=orphan,
                )
            )

    for record in manifest.assets:
        if record.ai_role is AIRole.UNKNOWN:
            findings.append(
                Finding(
                    code="provenance.unclassified",
                    severity=Severity.BLOCKER,
                    message=(
                        "Asset has no AI classification. An unclassified asset "
                        "cannot roll up into a KDP disclosure answer."
                    ),
                    subject=record.asset_id,
                )
            )
        if record.ai_role is AIRole.GENERATED and not record.prompt_version:
            findings.append(
                Finding(
                    code="provenance.no_prompt_version",
                    severity=Severity.MAJOR,
                    message=(
                        "AI-generated asset records no prompt version, so a "
                        "later prompt change cannot be traced to it."
                    ),
                    subject=record.asset_id,
                )
            )

    disclosure = manifest.disclosure()
    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "book_id": manifest.book_id,
            "art_pages": len(described),
            "assets": len(manifest.assets),
            "disclosure": disclosure.to_dict(),
        },
    )
