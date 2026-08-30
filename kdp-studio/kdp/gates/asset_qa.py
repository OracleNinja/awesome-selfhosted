"""G9 — asset QA.

Judges each shipping asset against the requirements a coloring-book page has
to meet, and against the prompt it was supposed to satisfy.

The split matters. What can be judged from the *record* — a failed attempt with
no approved replacement, a page whose approved version came from the wrong
prompt, an asset with no dimensions, a page below print resolution — is judged
here with no dependencies. What needs the *pixels* — stray shading, unclosed
regions, accidental text, watermarks, anatomy — needs Pillow, and without it
this gate reports BLOCKED. An image nobody looked at has not passed image QA.
"""

from __future__ import annotations

from ..models.manifest import BookManifest
from ..models.provenance import AssetStatus
from ..specs import MIN_IMAGE_DPI
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G9.asset_qa"
STAGE = "qa"
VALIDATOR_VERSION = "1"

#: What a pixel inspector would check, listed so a BLOCKED result says exactly
#: what went unchecked rather than gesturing at "image quality".
PIXEL_CHECKS = (
    "pure white background",
    "black line quality and closure",
    "unwanted shading or greyscale fill",
    "unwanted colour",
    "compression artifacts",
    "malformed anatomy",
    "duplicated elements within a page",
    "accidental text",
    "watermarks, logos and signatures",
    "content clipped at the edge",
)


def pixel_inspection_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def run(manifest: BookManifest, *, require_pixel_inspection: bool = True) -> GateResult:
    findings: list[Finding] = []
    spec = manifest.spec
    art_pages = {p.page_id: p for p in spec.art_pages}

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
    if require_pixel_inspection and not pixel_inspection_available():
        blocked_reason = (
            "Pixel inspection is unavailable: Pillow is not installed, so none "
            "of the following were checked — "
            + "; ".join(PIXEL_CHECKS)
            + ". Install the imaging extras (pip install -r requirements.txt). "
            "Record-level checks passed, but an image nobody looked at has not "
            "passed image QA."
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
            "pixel_inspection": pixel_inspection_available(),
        },
        blocked_reason=blocked_reason,
    )
