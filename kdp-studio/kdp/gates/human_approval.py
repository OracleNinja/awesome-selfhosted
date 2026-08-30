"""G14 — human approval.

The gate no automation can satisfy. It passes only when a person has recorded a
decision about *this exact book*, and it re-derives the fingerprint rather than
trusting the stored one, so a book edited after sign-off fails.

There is no flag, no environment variable and no argument that makes this gate
pass without an approval record. That is the point of it.
"""

from __future__ import annotations

from ..models.approval import ApprovalStatus
from ..models.manifest import BookManifest
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G14.human_approval"
STAGE = "human_approval"
VALIDATOR_VERSION = "1"


def run(manifest: BookManifest) -> GateResult:
    findings: list[Finding] = []
    approval = manifest.approval
    current = manifest.production_fingerprint()

    evidence = {
        "validator_version": VALIDATOR_VERSION,
        "book_id": manifest.book_id,
        "approval": approval.to_dict(),
        "production_fingerprint": current,
    }

    if approval.status is ApprovalStatus.PENDING:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason=(
                "Awaiting human approval. Generate the review package, have a "
                "person read it, and record their decision with "
                "`kdp approve` or `kdp reject`. No automation may satisfy this "
                "gate."
            ),
        )

    if approval.status is ApprovalStatus.REJECTED:
        findings.append(
            Finding(
                code="approval.rejected",
                severity=Severity.BLOCKER,
                message=(
                    f"Rejected by {approval.reviewer} on {approval.decided_at}"
                    + (f": {approval.notes}" if approval.notes else ".")
                ),
                subject="approval",
            )
        )
        return decide(GATE_ID, STAGE, findings, evidence=evidence)

    if approval.book_fingerprint != current:
        findings.append(
            Finding(
                code="approval.stale",
                severity=Severity.BLOCKER,
                message=(
                    "The book changed after it was approved, so the approval no "
                    "longer describes what would be published. Re-run the review "
                    "package and obtain a fresh decision."
                ),
                subject="approval",
                detail={
                    "approved_fingerprint": approval.book_fingerprint,
                    "current_fingerprint": current,
                },
            )
        )

    return decide(GATE_ID, STAGE, findings, evidence=evidence)
