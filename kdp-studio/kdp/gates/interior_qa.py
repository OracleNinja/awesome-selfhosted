"""G10 — interior QA.

Independent inspection of the built interior PDF: does the file that would be
uploaded match the book that was specified and approved?

The check that earns its keep is the hash comparison. An interior can be
rebuilt between assembly and QA — a page regenerated, a build re-run with a
changed spec — and a QA pass on a file that no longer exists is worse than no
QA at all. So this gate compares the recorded artefact hash against the file on
disk and stops if they differ.

A PDF that cannot be opened BLOCKS. "The reader is missing" is not "the PDF is
fine".

The reading is done by ``pypdf`` — a different implementation from the writer
in ``kdp/pdfwrite.py`` that produced the file. A writer that verified its own
output would certify its own bugs.
"""

from __future__ import annotations

from pathlib import Path

from ..inspection.checks import check_interior
from ..inspection.pdf import PDFLoadError, inspect_pdf, pdf_reader_available
from ..models.artifacts import ArtifactKind
from ..models.manifest import BookManifest
from ..models.provenance import hash_file
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G10.interior_qa"
STAGE = "production_qa"
VALIDATOR_VERSION = "2"

PDF_CHECKS = (
    "PDF opens and is not encrypted",
    "page count matches the specification",
    "page dimensions match trim plus bleed",
    "every interior page is the same size",
    "artwork present on exactly the pages the spec describes",
    "embedded image resolution at or above 300 DPI",
    "drawings on versos where single-sided art is declared",
)


def run(manifest: BookManifest) -> GateResult:
    findings: list[Finding] = []
    evidence: dict = {
        "validator_version": VALIDATOR_VERSION,
        "book_id": manifest.book_id,
        "pdf_reader": pdf_reader_available(),
    }

    artifact = manifest.artifact(ArtifactKind.INTERIOR_PDF)
    if artifact is None:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason=(
                "No interior PDF recorded on the manifest. Build it in the "
                "production stage before interior QA."
            ),
        )

    evidence["artifact"] = artifact.to_dict()
    path = Path(artifact.path)

    if not path.is_file():
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason=(
                f"Interior PDF is recorded at {artifact.path} but no file is "
                "there. The build output was moved or never written."
            ),
        )

    try:
        actual = hash_file(path)
    except OSError as exc:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason=f"The interior PDF could not be read: {exc}",
        )
    if actual != artifact.content_hash:
        findings.append(
            Finding(
                code="interior_qa.artifact_changed",
                severity=Severity.BLOCKER,
                message=(
                    "The interior PDF on disk is not the one recorded in the "
                    "manifest. It was rebuilt or edited after assembly, so any "
                    "earlier inspection describes a different file. Re-run "
                    "assembly, then QA."
                ),
                subject=artifact.path,
                detail={"recorded": artifact.content_hash, "actual": actual},
            )
        )

    approved = manifest.approved_assets
    art_pages = manifest.spec.art_pages
    if len(approved) < len(art_pages):
        findings.append(
            Finding(
                code="interior_qa.missing_approved_assets",
                severity=Severity.BLOCKER,
                message=(
                    f"{len(art_pages)} art pages but only {len(approved)} "
                    "approved assets; the interior cannot be complete."
                ),
                subject="assets",
            )
        )

    blocked_reason = None
    try:
        inspection = inspect_pdf(path)
        evidence["inspection"] = inspection.to_dict()
        for issue in check_interior(manifest, inspection):
            findings.append(
                Finding(
                    code=issue.code,
                    severity=Severity.BLOCKER if issue.blocking else Severity.MAJOR,
                    message=issue.message,
                    subject="interior",
                    detail=issue.detail,
                )
            )
        for note in inspection.not_checked:
            findings.append(
                Finding(
                    code="interior_qa.not_checked",
                    severity=Severity.INFO,
                    message=f"Not checked: {note}",
                    subject="interior",
                )
            )
    except PDFLoadError as exc:
        blocked_reason = (
            f"The interior PDF could not be inspected ({exc}), so none of the "
            "following were checked — " + "; ".join(PDF_CHECKS) + "."
        )

    return decide(GATE_ID, STAGE, findings, evidence=evidence, blocked_reason=blocked_reason)
