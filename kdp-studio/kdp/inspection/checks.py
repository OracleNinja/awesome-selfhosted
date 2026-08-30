"""Comparing a PDF against the book it is supposed to be.

Separate from :mod:`kdp.inspection.pdf`, which only measures. This module holds
the expectations — page count, page size, which pages carry artwork, cover
canvas and spine — and reports where the file departs from them.

Keeping measurement and expectation apart means a gate can report *both* what
the file is and what it should have been, which is the difference between "the
cover is wrong" and "the cover is 17.36 inches wide where 17.51 was expected,
because the interior gained 66 pages".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import SpecError
from ..models.manifest import BookManifest
from ..specs import (
    BLEED_IN,
    cover_geometry,
    get_paper,
    get_trim,
    interior_geometry,
)
from .pdf import DIMENSION_TOLERANCE_IN, PDFInspection


@dataclass(frozen=True, slots=True)
class PDFIssue:
    """One departure from what the book specifies."""

    code: str
    message: str
    blocking: bool = True
    detail: dict = field(default_factory=dict)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= DIMENSION_TOLERANCE_IN


def check_interior(
    manifest: BookManifest, inspection: PDFInspection
) -> tuple[PDFIssue, ...]:
    """Compare a built interior against its specification."""
    spec = manifest.spec
    issues: list[PDFIssue] = []

    if inspection.encrypted:
        issues.append(
            PDFIssue(
                "interior.encrypted",
                "The interior PDF is encrypted. KDP rejects encrypted files.",
            )
        )

    if inspection.page_count != spec.page_count:
        issues.append(
            PDFIssue(
                "interior.page_count_mismatch",
                f"The PDF has {inspection.page_count} pages; the specification "
                f"says {spec.page_count}. The cover's spine is computed from "
                "the spec, so these disagreeing means the cover is wrong too.",
                detail={
                    "pdf": inspection.page_count,
                    "spec": spec.page_count,
                },
            )
        )

    try:
        geometry = interior_geometry(get_trim(spec.trim), spec.page_count, spec.bleed)
    except SpecError as exc:
        # The specification itself is unprintable. G2 owns that, but this must
        # report rather than raise: an exception escaping a gate crashes the
        # run instead of stopping the book, which is the wrong failure.
        issues.append(
            PDFIssue(
                "interior.geometry_unresolvable",
                f"Cannot compare the PDF against the specification: {exc}",
            )
        )
        return tuple(issues)

    expected = (geometry.page_width_in, geometry.page_height_in)

    wrong = [
        page
        for page in inspection.pages
        if not (_close(page.width_in, expected[0]) and _close(page.height_in, expected[1]))
    ]
    if wrong:
        first = wrong[0]
        issues.append(
            PDFIssue(
                "interior.page_size_mismatch",
                f"{len(wrong)} page(s) are not "
                f"{expected[0]:.3f}x{expected[1]:.3f} in. Page {first.index} is "
                f"{first.width_in:.3f}x{first.height_in:.3f}. "
                + (
                    "A page short by 0.125 in usually means bleed was added to "
                    "the width but not to both the top and the bottom."
                    if spec.bleed
                    else "A no-bleed interior's pages equal the trim size."
                ),
                detail={
                    "expected": [round(expected[0], 4), round(expected[1], 4)],
                    "pages": [p.index for p in wrong[:20]],
                },
            )
        )

    if len(inspection.distinct_page_sizes) > 1:
        issues.append(
            PDFIssue(
                "interior.mixed_page_sizes",
                f"The interior mixes {len(inspection.distinct_page_sizes)} page "
                "sizes. KDP requires every interior page to be identical.",
                detail={"sizes": [list(s) for s in inspection.distinct_page_sizes]},
            )
        )

    # Which pages the spec says carry artwork, and which the file actually does.
    expected_art = {p.index for p in spec.art_pages}
    actual_art = set(inspection.pages_with_images())

    for missing in sorted(expected_art - actual_art):
        issues.append(
            PDFIssue(
                "interior.art_page_empty",
                f"Page {missing} should carry artwork but the PDF has no image "
                "on it.",
                detail={"page": missing},
            )
        )
    for extra in sorted(actual_art - expected_art):
        issues.append(
            PDFIssue(
                "interior.unexpected_image",
                f"Page {extra} carries an image the specification does not "
                "describe.",
                blocking=False,
                detail={"page": extra},
            )
        )

    # Effective resolution of the placed artwork over the live area.
    for page in inspection.pages:
        for width, height in page.image_sizes:
            dpi = min(
                width / geometry.live_width_in, height / geometry.live_height_in
            )
            if dpi < 300:
                issues.append(
                    PDFIssue(
                        "interior.low_resolution",
                        f"The image on page {page.index} resolves to {dpi:.0f} "
                        "DPI over the live area; KDP needs 300.",
                        detail={"page": page.index, "dpi": round(dpi, 1)},
                    )
                )

    if spec.single_sided_art:
        art_on_rectos = sorted(i for i in actual_art if i % 2 == 1)
        if art_on_rectos:
            issues.append(
                PDFIssue(
                    "interior.single_sided_violated",
                    f"{len(art_on_rectos)} drawing(s) sit on odd-numbered pages "
                    "in a book declared single-sided, so they back onto another "
                    "drawing and markers will bleed through.",
                    detail={"pages": art_on_rectos[:20]},
                )
            )

    return tuple(issues)


def check_cover(
    manifest: BookManifest, inspection: PDFInspection
) -> tuple[PDFIssue, ...]:
    """Compare a built cover against the geometry its page count implies."""
    spec = manifest.spec
    issues: list[PDFIssue] = []

    if inspection.encrypted:
        issues.append(
            PDFIssue("cover.encrypted", "The cover PDF is encrypted.")
        )

    if inspection.page_count != 1:
        issues.append(
            PDFIssue(
                "cover.page_count",
                f"A full-wrap cover is one page; this PDF has "
                f"{inspection.page_count}.",
                detail={"page_count": inspection.page_count},
            )
        )
        return tuple(issues)

    try:
        cover = cover_geometry(
            get_trim(spec.trim), get_paper(spec.paper), spec.page_count
        )
    except SpecError as exc:
        issues.append(
            PDFIssue(
                "cover.geometry_unresolvable",
                f"Cannot compare the cover against the specification: {exc}",
            )
        )
        return tuple(issues)

    page = inspection.pages[0]

    if not _close(page.width_in, cover.total_width_in):
        # The spine is the usual culprit: it is the only part of the width that
        # moves when the interior changes.
        implied_spine = page.width_in - (2 * BLEED_IN) - (2 * get_trim(spec.trim).width_in)
        issues.append(
            PDFIssue(
                "cover.width_mismatch",
                f"The cover is {page.width_in:.4f} in wide; "
                f"{cover.total_width_in:.4f} in is required for a "
                f"{spec.page_count}-page book on {spec.paper}. That implies a "
                f"spine of {implied_spine:.4f} in against the required "
                f"{cover.spine_width_in:.4f} in — rebuild the cover against the "
                "current page count.",
                detail={
                    "actual_width_in": round(page.width_in, 4),
                    "expected_width_in": cover.total_width_in,
                    "implied_spine_in": round(implied_spine, 4),
                    "expected_spine_in": cover.spine_width_in,
                },
            )
        )

    if not _close(page.height_in, cover.total_height_in):
        issues.append(
            PDFIssue(
                "cover.height_mismatch",
                f"The cover is {page.height_in:.4f} in tall; "
                f"{cover.total_height_in:.4f} in is required (trim plus bleed "
                "at the top and the bottom).",
                detail={
                    "actual_height_in": round(page.height_in, 4),
                    "expected_height_in": cover.total_height_in,
                },
            )
        )

    if cover.spine_text_allowed and cover.spine_text_width_in <= 0:
        issues.append(
            PDFIssue(
                "cover.spine_text_unsafe",
                "Spine text is permitted at this page count but the spine is "
                "too narrow to hold it inside the safety margins.",
                detail={"spine_width_in": cover.spine_width_in},
            )
        )

    if not page.has_text:
        issues.append(
            PDFIssue(
                "cover.no_text",
                "No text was found on the cover. A cover with no title is "
                "almost certainly a build that went wrong.",
                blocking=False,
            )
        )

    return tuple(issues)
