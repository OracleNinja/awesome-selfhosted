"""G2 — specification legality.

Checks the book against the KDP manufacturing envelope *before* anything is
generated, which is the cheapest possible moment to discover that a 20-page
book cannot be printed or that a chosen trim is non-standard.

It also refuses to certify a book while the print-specification tables are
unverified. That is a deliberate stop: a green preflight computed from numbers
nobody checked is worse than no preflight, because it is trusted.
"""

from __future__ import annotations

from ..errors import SpecError
from ..models.spec import BookSpec
from ..specs import cover_geometry, get_paper, get_trim, interior_geometry
from ..specs.revision import verification_problem

#: The tables this gate's arithmetic actually rests on. Scoped deliberately:
#: an unverified royalty table says nothing about whether a book is printable,
#: and blocking on it here would make the message misleading.
REQUIRED_TABLES = ("trim", "paper", "interior", "cover")
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G2.spec_legality"
STAGE = "concept"


def run(spec: BookSpec, *, require_verified_specs: bool = True) -> GateResult:
    findings: list[Finding] = []
    evidence: dict = {"book_id": spec.book_id}

    if require_verified_specs:
        problem = verification_problem(*REQUIRED_TABLES)
        if problem:
            findings.append(
                Finding(
                    code="spec.tables_unverified",
                    severity=Severity.BLOCKER,
                    message=problem,
                    detail={"tables": list(REQUIRED_TABLES)},
                )
            )

    try:
        trim = get_trim(spec.trim)
    except SpecError as exc:
        findings.append(
            Finding(
                code="spec.unknown_trim",
                severity=Severity.BLOCKER,
                message=str(exc),
                subject="trim",
            )
        )
        trim = None

    try:
        paper = get_paper(spec.paper)
    except SpecError as exc:
        findings.append(
            Finding(
                code="spec.unknown_paper",
                severity=Severity.BLOCKER,
                message=str(exc),
                subject="paper",
            )
        )
        paper = None

    if trim is not None and not trim.standard:
        findings.append(
            Finding(
                code="spec.non_standard_trim",
                severity=Severity.MAJOR,
                message=(
                    f"Trim {trim.label} is not a KDP standard size, which "
                    "narrows distribution and can attract a surcharge."
                ),
                subject="trim",
            )
        )

    if paper is not None and not paper.supports_page_count(spec.page_count):
        findings.append(
            Finding(
                code="spec.page_count_out_of_range",
                severity=Severity.BLOCKER,
                message=(
                    f"{spec.page_count} pages is outside the printable range "
                    f"for {paper.label} ({paper.min_pages}-{paper.max_pages})."
                ),
                subject="page_count",
                detail={
                    "page_count": spec.page_count,
                    "min": paper.min_pages,
                    "max": paper.max_pages,
                },
            )
        )

    if spec.pages and not spec.art_pages:
        findings.append(
            Finding(
                code="spec.no_art_pages",
                severity=Severity.BLOCKER,
                message=(
                    f"{len(spec.pages)} pages are described and none of them is "
                    "art. A low-content book with no drawings has nothing to "
                    "offer a buyer."
                ),
                subject="pages",
            )
        )

    declared = len(spec.pages)
    if declared and declared != spec.page_count:
        findings.append(
            Finding(
                code="spec.page_count_mismatch",
                severity=Severity.BLOCKER,
                message=(
                    f"page_count is {spec.page_count} but {declared} pages are "
                    "described. The interior would not match the cover."
                ),
                subject="page_count",
                detail={"declared": spec.page_count, "described": declared},
            )
        )

    indices = [p.index for p in spec.pages]
    if declared and sorted(indices) != list(range(1, declared + 1)):
        findings.append(
            Finding(
                code="spec.page_indices_not_contiguous",
                severity=Severity.BLOCKER,
                message=(
                    "Page indices are not a contiguous 1..N run; the interior "
                    "order is ambiguous."
                ),
                subject="pages",
            )
        )

    page_ids = [p.page_id for p in spec.pages]
    duplicates = sorted({pid for pid in page_ids if page_ids.count(pid) > 1})
    if duplicates:
        findings.append(
            Finding(
                code="spec.duplicate_page_ids",
                severity=Severity.BLOCKER,
                message=f"Duplicate page ids: {', '.join(duplicates)}.",
                subject="pages",
                detail={"duplicates": duplicates},
            )
        )

    # A coloring book printed double-sided lets marker bleed ruin the drawing
    # on the reverse. This is the single most common one-star review for the
    # category, so it is a first-class check rather than a note in a doc.
    if spec.single_sided_art:
        art_on_versos = [p.index for p in spec.art_pages if p.index % 2 == 1]
        if art_on_versos:
            findings.append(
                Finding(
                    code="spec.single_sided_violated",
                    severity=Severity.MAJOR,
                    message=(
                        "Book declares single-sided art but places art on "
                        f"{len(art_on_versos)} odd-numbered page(s). With "
                        "single-sided art every drawing should back onto a "
                        "blank."
                    ),
                    subject="pages",
                    detail={"pages": art_on_versos[:20]},
                )
            )

    if trim is not None and paper is not None:
        try:
            interior = interior_geometry(trim, spec.page_count, spec.bleed)
            cover = cover_geometry(trim, paper, spec.page_count)
            evidence["interior"] = interior.to_dict()
            evidence["cover"] = cover.to_dict()
        except SpecError as exc:
            findings.append(
                Finding(
                    code="spec.geometry_undefined",
                    severity=Severity.BLOCKER,
                    message=str(exc),
                )
            )

    return decide(GATE_ID, STAGE, findings, evidence=evidence)
