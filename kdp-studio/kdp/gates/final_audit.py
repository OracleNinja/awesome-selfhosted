"""G13 — the final publishing audit.

Read-only, and answers one question: could a human reasonably submit this book?

It re-runs the cheap invariants rather than trusting the stored reports,
because a stored PASS describes the book as it was when the gate ran. The
manifest can change afterwards. An audit that reads its own earlier conclusions
is not an audit.

It never submits anything, and nothing downstream of it does either.
"""

from __future__ import annotations

from pathlib import Path

from ..models.artifacts import MATERIAL_ARTIFACTS
from ..models.manifest import BookManifest
from ..models.provenance import hash_file
from ..specs.revision import verification_problem
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G13.final_audit"
STAGE = "final_audit"
VALIDATOR_VERSION = "1"


def run(manifest: BookManifest, *, require_verified_specs: bool = True) -> GateResult:
    findings: list[Finding] = []
    spec = manifest.spec
    evidence: dict = {
        "validator_version": VALIDATOR_VERSION,
        "book_id": manifest.book_id,
        "stage": manifest.stage,
    }

    if require_verified_specs:
        # The final audit is the one place that requires *every* table: it is
        # certifying the whole book, geometry and policy and economics alike.
        problem = verification_problem()
        if problem:
            return decide(
                GATE_ID,
                STAGE,
                [],
                evidence=evidence,
                blocked_reason=problem,
            )

    # 1. Every art page ships exactly one asset.
    art_pages = {p.page_id for p in spec.art_pages}
    for page_id in sorted(art_pages):
        if manifest.approved_for(page_id) is None:
            findings.append(
                Finding(
                    code="final.page_not_ready",
                    severity=Severity.BLOCKER,
                    message="Art page has no approved asset.",
                    subject=page_id,
                )
            )

    # 2. Both material artefacts exist and match their recorded hashes.
    for kind in MATERIAL_ARTIFACTS:
        artifact = manifest.artifact(kind)
        if artifact is None:
            findings.append(
                Finding(
                    code="final.artifact_missing",
                    severity=Severity.BLOCKER,
                    message=f"No {kind.value} recorded on the manifest.",
                    subject=kind.value,
                )
            )
            continue
        path = Path(artifact.path)
        if not path.is_file():
            findings.append(
                Finding(
                    code="final.artifact_file_missing",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{kind.value} is recorded at {artifact.path} but there "
                        "is no readable file there."
                    ),
                    subject=kind.value,
                )
            )
            continue

        try:
            digest = hash_file(path)
        except OSError as exc:
            findings.append(
                Finding(
                    code="final.artifact_unreadable",
                    severity=Severity.BLOCKER,
                    message=f"{kind.value} could not be read: {exc}",
                    subject=kind.value,
                )
            )
            continue

        if digest != artifact.content_hash:
            findings.append(
                Finding(
                    code="final.artifact_changed",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{kind.value} on disk differs from the manifest record; "
                        "it changed after production."
                    ),
                    subject=kind.value,
                )
            )

    # 3. The disclosure answer is determinable.
    disclosure = manifest.disclosure()
    evidence["disclosure"] = disclosure.to_dict()
    evidence["disclosure_statement"] = disclosure.statement()
    if not disclosure.complete:
        findings.append(
            Finding(
                code="final.disclosure_incomplete",
                severity=Severity.BLOCKER,
                message=(
                    "The KDP AI-content question cannot be answered: "
                    f"{len(disclosure.unclassified)} shipping asset(s) "
                    "unclassified."
                ),
                subject="ai_disclosure",
            )
        )

    # 4. There is a listing and a price.
    if manifest.metadata is None:
        findings.append(
            Finding(
                code="final.no_metadata",
                severity=Severity.BLOCKER,
                message="No listing metadata.",
                subject="metadata",
            )
        )
    if manifest.economics is None or manifest.economics.selected_scenario is None:
        findings.append(
            Finding(
                code="final.no_price",
                severity=Severity.BLOCKER,
                message="No pricing scenario selected.",
                subject="economics",
            )
        )
    else:
        chosen = manifest.economics.selected_scenario
        evidence["price"] = chosen.to_dict()
        if not chosen.viable:
            findings.append(
                Finding(
                    code="final.price_not_viable",
                    severity=Severity.BLOCKER,
                    message=(
                        f"At {chosen.label} the net royalty is "
                        f"{chosen.net_royalty_usd:.2f}; every copy sold loses "
                        "money."
                    ),
                    subject="economics",
                )
            )
        findings.append(
            Finding(
                code="final.economics_are_projections",
                severity=Severity.INFO,
                message=(
                    "Royalty figures are projections at confidence "
                    f"{manifest.economics.confidence.value!r}, not income."
                ),
                subject="economics",
            )
        )

    # 5. Approval is a separate gate; here we only report its state, because
    #    a final audit that demanded approval would invert the order — the
    #    human reads this audit in order to decide.
    evidence["approval"] = manifest.approval.to_dict()
    evidence["approval_is_current"] = manifest.approval_is_current()
    evidence["production_fingerprint"] = manifest.production_fingerprint()

    result = decide(GATE_ID, STAGE, findings, evidence=evidence)
    return result


def verdict(result: GateResult) -> str:
    """FINAL_PASS / FINAL_FAIL / FINAL_BLOCKED, for the human-facing report."""
    return {"pass": "FINAL_PASS", "fail": "FINAL_FAIL", "blocked": "FINAL_BLOCKED"}[
        result.status.value
    ]
