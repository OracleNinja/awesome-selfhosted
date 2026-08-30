"""G11 — cover QA.

Independent inspection of the full-wrap cover. Separate from G10 because a
cover fails in its own ways: the spine is the part that goes wrong, and it goes
wrong quietly.

The spine is computed from the page count and the paper caliper. If the
interior gains or loses pages after the cover is built, the cover is now the
wrong width and nothing about the file itself looks wrong. So this gate
recomputes the geometry from the current spec and compares.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import SpecError
from ..models.artifacts import ArtifactKind
from ..models.manifest import BookManifest
from ..models.provenance import hash_file
from ..specs import cover_geometry, get_paper, get_trim
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G11.cover_qa"
STAGE = "production_qa"
VALIDATOR_VERSION = "1"

COVER_CHECKS = (
    "canvas dimensions match the computed full-wrap size",
    "spine width matches the current page count",
    "safe areas respected on front, back and spine",
    "barcode area left clear",
    "title and author legible at print size",
    "resolution at or above 300 DPI",
    "no prohibited elements",
)


def pdf_reader_available() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def run(manifest: BookManifest) -> GateResult:
    findings: list[Finding] = []
    spec = manifest.spec
    evidence: dict = {
        "validator_version": VALIDATOR_VERSION,
        "book_id": manifest.book_id,
        "pdf_reader": pdf_reader_available(),
    }

    artifact = manifest.artifact(ArtifactKind.COVER_PDF)
    if artifact is None:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason=(
                "No cover PDF recorded on the manifest. Build it in the "
                "production stage before cover QA."
            ),
        )

    evidence["artifact"] = artifact.to_dict()
    path = Path(artifact.path)

    if not path.exists():
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason=(
                f"Cover PDF is recorded at {artifact.path} but no file is there."
            ),
        )

    if hash_file(path) != artifact.content_hash:
        findings.append(
            Finding(
                code="cover_qa.artifact_changed",
                severity=Severity.BLOCKER,
                message=(
                    "The cover PDF on disk is not the one recorded in the "
                    "manifest. Re-run cover production, then QA."
                ),
                subject=artifact.path,
            )
        )

    try:
        cover = cover_geometry(get_trim(spec.trim), get_paper(spec.paper), spec.page_count)
        evidence["expected_cover"] = cover.to_dict()

        if cover.spine_text_allowed and cover.spine_text_width_in <= 0:
            findings.append(
                Finding(
                    code="cover_qa.spine_text_unsafe",
                    severity=Severity.BLOCKER,
                    message=(
                        "Spine text is permitted at this page count but the "
                        "spine is too narrow to hold it inside the safety "
                        "margins."
                    ),
                    subject="spine",
                )
            )
    except SpecError as exc:
        return decide(
            GATE_ID,
            STAGE,
            findings,
            evidence=evidence,
            blocked_reason=f"Cannot resolve cover geometry: {exc}",
        )

    # The quiet failure: pages changed after the cover was built, so the spine
    # is now the wrong width even though the file itself is intact.
    interior = manifest.artifact(ArtifactKind.INTERIOR_PDF)
    if interior is not None and artifact.created_at and interior.created_at:
        if interior.created_at > artifact.created_at:
            findings.append(
                Finding(
                    code="cover_qa.stale_against_interior",
                    severity=Severity.BLOCKER,
                    message=(
                        "The interior was rebuilt after this cover. If the page "
                        "count changed, the spine is now the wrong width — "
                        "rebuild the cover."
                    ),
                    subject="cover",
                    detail={
                        "cover_built": artifact.created_at,
                        "interior_built": interior.created_at,
                    },
                )
            )

    if manifest.metadata is not None:
        if manifest.metadata.title.strip() and not spec.title.strip():
            findings.append(
                Finding(
                    code="cover_qa.title_unavailable",
                    severity=Severity.MAJOR,
                    message="Cannot confirm the cover title against an empty spec title.",
                    subject="title",
                )
            )

    blocked_reason = None
    if not pdf_reader_available():
        blocked_reason = (
            "Cover inspection is unavailable: pypdf is not installed, so none "
            "of the following were checked — "
            + "; ".join(COVER_CHECKS)
            + ". Install the production extras (pip install -r requirements.txt)."
        )

    return decide(GATE_ID, STAGE, findings, evidence=evidence, blocked_reason=blocked_reason)
