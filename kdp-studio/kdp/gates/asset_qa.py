"""G9 — asset QA.

Judges each shipping asset against the requirements a coloring-book page has
to meet, and against the prompt it was supposed to satisfy.

The split matters. What can be judged from the *record* — a failed attempt with
no approved replacement, a page whose approved version came from the wrong
prompt, an asset with no dimensions, a page below print resolution — is judged
without opening a file. Everything else needs the pixels, and those are read
and measured by :mod:`kdp.inspection.image`.

Capability is decided per artefact, not per install. A PNG can be read with the
standard library alone; a JPEG needs Pillow. So a page is only unchecked when
*that page* could not be loaded — and an image nobody could look at has not
passed image QA, so the gate blocks.

One check stays honestly unavailable: detecting accidental text needs OCR,
which is not installed. The gate reports that it did not look rather than
reporting that it found nothing.
"""

from __future__ import annotations

from pathlib import Path

from ..inspection.image import ImageLoadError, inspect_image
from ..models.manifest import BookManifest
from ..models.provenance import AssetStatus
from ..specs import MIN_IMAGE_DPI
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G9.asset_qa"
STAGE = "qa"
VALIDATOR_VERSION = "3"

#: What the pixel pass measures, named so a report says what was covered.
PIXEL_CHECKS = (
    "pure white background",
    "black line presence and ink coverage",
    "unwanted shading or greyscale fill",
    "unwanted colour",
    "content clipped at the page edge",
    "closed regions — whether a fill would stay inside the outlines",
    "watermark, logo or signature marks (corner heuristic)",
    "pixel dimensions and effective print resolution",
)


def pixel_inspection_available() -> bool:
    """Whether the optional decoder is present.

    Kept because callers and tests refer to it, but it is no longer what
    decides whether the gate can run: PNGs are readable without it. Whether a
    *particular* page could be inspected is answered by trying.
    """
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def run(
    manifest: BookManifest,
    *,
    require_pixel_inspection: bool = True,
    assets_dir: str | Path | None = None,
) -> GateResult:
    spec = manifest.spec
    art_pages = {p.page_id: p for p in spec.art_pages}
    if not art_pages:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence={"validator_version": VALIDATOR_VERSION, "book_id": manifest.book_id},
            blocked_reason=(
                "The book describes no art pages, so no artwork was inspected. "
                "A coloring book with no drawings has not passed asset QA — it "
                "has bypassed it."
            ),
        )

    findings: list[Finding] = []
    uninspectable: list[str] = []
    inspected = 0

    for page_id, page in sorted(art_pages.items()):
        attempts = manifest.attempts(page_id)
        approved = manifest.approved_for(page_id)

        if not attempts:
            findings.append(
                Finding(
                    code="asset_qa.no_attempts",
                    severity=Severity.BLOCKER,
                    message="No generation attempt recorded for this page.",
                    subject=page_id,
                )
            )
            continue

        if approved is None:
            latest = attempts[-1]
            findings.append(
                Finding(
                    code="asset_qa.no_approved_version",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{len(attempts)} attempt(s), none approved (latest is "
                        f"{latest.status.value}). The page has nothing to print."
                    ),
                    subject=page_id,
                    detail={
                        "attempts": len(attempts),
                        "latest_status": latest.status.value,
                        "latest_failure": latest.failure_reason,
                    },
                )
            )
            continue

        shipping = [a for a in attempts if a.ships]
        if len(shipping) > 1:
            findings.append(
                Finding(
                    code="asset_qa.multiple_approved",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{len(shipping)} versions of this page are marked "
                        "approved; which one prints is ambiguous."
                    ),
                    subject=page_id,
                    detail={"assets": sorted(a.asset_id for a in shipping)},
                )
            )

        if manifest.prompt_plan is not None:
            prompt = manifest.prompt_plan.for_page(page_id)
            if prompt is not None and approved.prompt_id not in (None, prompt.prompt_id):
                findings.append(
                    Finding(
                        code="asset_qa.prompt_mismatch",
                        severity=Severity.MAJOR,
                        message=(
                            "Approved version was produced from a prompt that is "
                            "not the one planned for this page."
                        ),
                        subject=page_id,
                        detail={
                            "asset_prompt": approved.prompt_id,
                            "planned_prompt": prompt.prompt_id,
                        },
                    )
                )

        if approved.width is None or approved.height is None:
            findings.append(
                Finding(
                    code="asset_qa.no_dimensions",
                    severity=Severity.MAJOR,
                    message=(
                        "Approved version records no pixel dimensions, so its "
                        "print resolution cannot be checked."
                    ),
                    subject=page_id,
                )
            )
        else:
            # Effective DPI against the live area the page will occupy.
            from ..errors import SpecError
            from ..specs import get_trim, interior_geometry

            try:
                geometry = interior_geometry(
                    get_trim(spec.trim), spec.page_count, spec.bleed
                )
                dpi = min(
                    approved.width / geometry.live_width_in,
                    approved.height / geometry.live_height_in,
                )
                if dpi < MIN_IMAGE_DPI:
                    findings.append(
                        Finding(
                            code="asset_qa.below_print_resolution",
                            severity=Severity.BLOCKER,
                            message=(
                                f"Approved version resolves to {dpi:.0f} DPI over "
                                f"the live area; KDP needs at least "
                                f"{MIN_IMAGE_DPI}."
                            ),
                            subject=page_id,
                            detail={"effective_dpi": round(dpi, 1)},
                        )
                    )
            except SpecError:
                # Geometry problems are G2/G5's to report; not duplicated here.
                pass

        # --- the pixel pass ------------------------------------------------
        if not require_pixel_inspection:
            continue

        source = _resolve(approved.path, assets_dir)
        if source is None:
            uninspectable.append(
                f"{page_id}: the approved version records no readable path"
            )
            continue

        try:
            inspection = inspect_image(source)
        except ImageLoadError as exc:
            uninspectable.append(f"{page_id}: {exc}")
            continue

        inspected += 1
        for issue in inspection.problems():
            findings.append(
                Finding(
                    code=issue.code,
                    severity=Severity.BLOCKER if issue.blocking else Severity.MAJOR,
                    message=issue.message,
                    subject=page_id,
                    detail=issue.detail,
                )
            )

        # The recorded dimensions must match the file. A record that disagrees
        # with its own artwork is how a resolution check passes on a page that
        # was later replaced with a smaller one.
        if (
            approved.width is not None
            and (approved.width, approved.height) != (inspection.width, inspection.height)
        ):
            findings.append(
                Finding(
                    code="asset_qa.dimensions_disagree",
                    severity=Severity.BLOCKER,
                    message=(
                        f"Provenance records {approved.width}x{approved.height} "
                        f"but the file is {inspection.width}x{inspection.height}."
                    ),
                    subject=page_id,
                )
            )

    failed = [a for a in manifest.assets if a.status is AssetStatus.FAILED]
    for record in failed:
        findings.append(
            Finding(
                code="asset_qa.generation_failure_recorded",
                severity=Severity.INFO,
                message=f"Attempt failed: {record.failure_reason}",
                subject=record.page,
            )
        )

    blocked_reason = None
    if not require_pixel_inspection:
        blocked_reason = (
            "Pixel inspection was skipped by request, so none of the following "
            "were checked — " + "; ".join(PIXEL_CHECKS) + ". Record-level checks "
            "ran, but an image nobody looked at has not passed image QA."
        )
    elif uninspectable:
        blocked_reason = (
            f"{len(uninspectable)} page(s) could not be inspected, so none of "
            "the following were checked for them — "
            + "; ".join(PIXEL_CHECKS)
            + ". "
            + "; ".join(uninspectable[:5])
        )

    if require_pixel_inspection and inspected:
        findings.append(
            Finding(
                code="asset_qa.text_detection_unavailable",
                severity=Severity.INFO,
                message=(
                    "Accidental text was not checked: that needs OCR, which is "
                    "not installed. Everything else in the pixel pass ran."
                ),
            )
        )

    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "validator_version": VALIDATOR_VERSION,
            "book_id": manifest.book_id,
            "art_pages": len(art_pages),
            "total_attempts": len(manifest.assets),
            "approved": len(manifest.approved_assets),
            "failed_attempts": len(failed),
            "pages_inspected": inspected,
            "pages_uninspectable": len(uninspectable),
            "pixel_checks": list(PIXEL_CHECKS),
            "optional_decoder": pixel_inspection_available(),
        },
        blocked_reason=blocked_reason,
    )


def _resolve(path: str | None, assets_dir: str | Path | None) -> Path | None:
    """Find an asset on disk from its recorded path.

    Manifests store a book-relative path (``assets/PAGE-004.a001.png``). The
    caller supplies where that book lives; without it, only an already-absolute
    or cwd-relative path can be found.
    """
    if not path:
        return None
    candidate = Path(path)
    if assets_dir is not None:
        rooted = Path(assets_dir) / candidate.name
        if rooted.is_file():
            return rooted
    return candidate if candidate.is_file() else None
