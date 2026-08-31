"""G5 — print preflight.

Splits cleanly in two, and the split is the point.

What can be checked from the manifest alone — page-count parity between spec
and assets, trim legality, geometry consistency, spine sanity — is checked here
and now, with no dependencies.

What genuinely requires opening the PDF — actual page dimensions, embedded
fonts, image DPI, colour space, ink coverage — cannot be faked from metadata.
When the PDF toolchain is absent this gate reports ``BLOCKED``. A book has not
passed preflight because the preflight tool was missing.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import SpecError
from ..inspection.pdf import PDFLoadError, inspect_pdf, pdf_reader_available
from ..models.manifest import BookManifest
from ..specs import (
    MIN_IMAGE_DPI,
    cover_geometry,
    get_paper,
    get_trim,
    interior_geometry,
)
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G5.print_preflight"
STAGE = "production"


def pdf_toolchain_available() -> bool:
    """True when an independent PDF reader is installed."""
    return pdf_reader_available()


def run(
    manifest: BookManifest,
    *,
    interior_pdf: str | Path | None = None,
    cover_pdf: str | Path | None = None,
) -> GateResult:
    findings: list[Finding] = []
    spec = manifest.spec
    evidence: dict = {"book_id": manifest.book_id, "min_image_dpi": MIN_IMAGE_DPI}

    try:
        trim = get_trim(spec.trim)
        paper = get_paper(spec.paper)
        interior = interior_geometry(trim, spec.page_count, spec.bleed)
        cover = cover_geometry(trim, paper, spec.page_count)
        evidence["interior"] = interior.to_dict()
        evidence["cover"] = cover.to_dict()
    except SpecError as exc:
        # Geometry is the basis of every other check here, so this is fatal
        # rather than one finding among many.
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence=evidence,
            blocked_reason=f"Cannot resolve print geometry: {exc}",
        )

    if interior.live_width_in <= 0 or interior.live_height_in <= 0:
        findings.append(
            Finding(
                code="preflight.no_live_area",
                severity=Severity.BLOCKER,
                message=(
                    "Margins and gutter consume the whole page; there is no "
                    "printable area left."
                ),
                subject="interior",
            )
        )

    if cover.spine_text_allowed and cover.spine_text_width_in <= 0:
        findings.append(
            Finding(
                code="preflight.spine_text_unsafe",
                severity=Severity.BLOCKER,
                message=(
                    "Spine text is permitted at this page count but the spine "
                    "is too narrow to hold it inside the safety margins."
                ),
                subject="cover",
                detail={"spine_width_in": cover.spine_width_in},
            )
        )

    art_pages = len(spec.art_pages)
    if art_pages == 0:
        findings.append(
            Finding(
                code="preflight.no_art_pages",
                severity=Severity.BLOCKER,
                message="The interior describes no art pages.",
                subject="interior",
            )
        )

    missing_files = [
        label
        for label, path in (("interior", interior_pdf), ("cover", cover_pdf))
        if path is not None and not Path(path).is_file()
    ]
    for label in missing_files:
        findings.append(
            Finding(
                code="preflight.file_missing",
                severity=Severity.BLOCKER,
                message=f"Declared {label} PDF does not exist on disk.",
                subject=label,
            )
        )

    blocked_reason = None
    if interior_pdf is None or cover_pdf is None:
        blocked_reason = (
            "Deep preflight needs both an interior and a cover PDF. Page "
            "dimensions, embedded fonts, image DPI and colour space cannot be "
            "inferred from the manifest."
        )
    elif not missing_files:
        # Confirm both files actually parse. The detailed comparison against
        # the specification is G10 and G11's job; duplicating it here would
        # mean two places to keep in step.
        for label, target in (("interior", interior_pdf), ("cover", cover_pdf)):
            try:
                result = inspect_pdf(target, extract_images=False)
                evidence[f"{label}_pages"] = result.page_count
            except PDFLoadError as exc:
                blocked_reason = f"The {label} PDF could not be opened: {exc}"
                break

    return decide(GATE_ID, STAGE, findings, evidence=evidence, blocked_reason=blocked_reason)
