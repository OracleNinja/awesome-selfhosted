"""G3 — provenance completeness.

Every page the spec describes must have an asset with a provenance record, and
every record must be classified. This gate is what makes the disclosure answer
in G6 trustworthy: it is checked once, here, before QA spends any time on
artwork that may not be accounted for.
"""

from __future__ import annotations

from ..models.manifest import BookManifest
from ..models.provenance import AIRole, AssetStatus
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G3.provenance_complete"
STAGE = "generation"
VALIDATOR_VERSION = "2"


def run(manifest: BookManifest) -> GateResult:
    """Check that every art page is accounted for by a classified asset.

    Works in terms of *pages*, not version ids. An asset id identifies one
    attempt (``PAGE-004.a002``); the page it belongs to is what the spec
    describes, and confusing the two is how a fully generated book comes back
    reading as entirely missing.
    """
    findings: list[Finding] = []

    described = {p.page_id for p in manifest.spec.pages if p.role == "art"}
    with_attempts = {a.page for a in manifest.assets}

    for missing in sorted(described - with_attempts):
        findings.append(
            Finding(
                code="provenance.missing_record",
                severity=Severity.BLOCKER,
                message="Art page has no provenance record.",
                subject=missing,
            )
        )

    for orphan in sorted(with_attempts - described):
        records = [a for a in manifest.assets if a.page == orphan]
        # Assets that are not art pages (cover, metadata copy) are legitimate;
        # only flag ones that claim to be pages the spec never mentioned.
        if any(r.kind.value == "image" for r in records):
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

    # Only versions that ship need classifying: a discarded attempt is not in
    # the book, so its AI role cannot affect the disclosure answer.
    for record in manifest.approved_assets:
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

    # An attempt left PENDING has been produced but never judged. It must not
    # reach QA looking like a decision was made about it.
    pending = sorted(
        a.asset_id for a in manifest.assets if a.status is AssetStatus.PENDING
    )
    for asset_id in pending:
        findings.append(
            Finding(
                code="provenance.unjudged_attempt",
                severity=Severity.MAJOR,
                message=(
                    "Attempt is still pending: it was generated but never "
                    "approved or rejected."
                ),
                subject=asset_id,
            )
        )

    disclosure = manifest.disclosure()
    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "validator_version": VALIDATOR_VERSION,
            "book_id": manifest.book_id,
            "art_pages": len(described),
            "attempts": len(manifest.assets),
            "shipping": len(manifest.approved_assets),
            "pending": len(pending),
            "disclosure": disclosure.to_dict(),
        },
    )
