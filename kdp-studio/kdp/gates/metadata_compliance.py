"""G6 — metadata and AI-disclosure completeness.

The last gate before a human touches the KDP form. Two jobs:

**The disclosure answer must be complete.** Derived from asset provenance, and
incomplete if any asset is unclassified. This gate will not let a book reach
the submission checklist with an unanswerable disclosure question.

**The listing must not look like spam.** Keyword stuffing and misleading
metadata are named removal triggers for low-content books, and they are also
the easiest thing to do by accident when metadata is machine-assembled.
"""

from __future__ import annotations

from ..models.manifest import BookManifest
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G6.metadata_compliance"
STAGE = "publishing_prep"

#: KDP offers seven keyword slots.
MAX_KEYWORDS = 7
MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 4000
MIN_DESCRIPTION_CHARS = 100

#: A keyword slot holding this many comma-separated terms is being stuffed
#: rather than used.
MAX_TERMS_PER_KEYWORD = 5


def run(manifest: BookManifest) -> GateResult:
    findings: list[Finding] = []
    metadata = manifest.metadata

    if metadata is None:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence={"book_id": manifest.book_id},
            blocked_reason="No metadata attached to the manifest.",
        )

    disclosure = manifest.disclosure()
    if not disclosure.complete:
        findings.append(
            Finding(
                code="metadata.disclosure_incomplete",
                severity=Severity.BLOCKER,
                message=(
                    f"{len(disclosure.unclassified)} asset(s) are unclassified, "
                    "so the KDP AI-content question cannot be answered "
                    "truthfully."
                ),
                subject="ai_disclosure",
                detail={"unclassified": list(disclosure.unclassified)},
            )
        )
    elif disclosure.required and not metadata.ai_disclosure_confirmed:
        findings.append(
            Finding(
                code="metadata.disclosure_unconfirmed",
                severity=Severity.BLOCKER,
                message=(
                    "Book contains AI-generated content "
                    f"({', '.join(disclosure.categories)}) but no human has "
                    "confirmed the disclosure. Set ai_disclosure_confirmed "
                    "once the KDP form has been answered."
                ),
                subject="ai_disclosure",
            )
        )

    if not metadata.title.strip():
        findings.append(
            Finding(
                code="metadata.no_title",
                severity=Severity.BLOCKER,
                message="Listing has no title.",
                subject="title",
            )
        )
    elif len(metadata.title) > MAX_TITLE_CHARS:
        findings.append(
            Finding(
                code="metadata.title_too_long",
                severity=Severity.BLOCKER,
                message=f"Title exceeds {MAX_TITLE_CHARS} characters.",
                subject="title",
                detail={"length": len(metadata.title)},
            )
        )

    if metadata.title.strip() and metadata.title != manifest.spec.title:
        findings.append(
            Finding(
                code="metadata.title_drift",
                severity=Severity.MINOR,
                message=(
                    "Listing title differs from the spec title. Confirm this "
                    "is intentional."
                ),
                subject="title",
            )
        )

    description = metadata.description.strip()
    if len(description) < MIN_DESCRIPTION_CHARS:
        findings.append(
            Finding(
                code="metadata.description_too_short",
                severity=Severity.MAJOR,
                message=(
                    f"Description is {len(description)} characters. A thin "
                    "description reads as a mass-generated listing."
                ),
                subject="description",
            )
        )
    elif len(description) > MAX_DESCRIPTION_CHARS:
        findings.append(
            Finding(
                code="metadata.description_too_long",
                severity=Severity.BLOCKER,
                message=f"Description exceeds {MAX_DESCRIPTION_CHARS} characters.",
                subject="description",
                detail={"length": len(description)},
            )
        )

    if len(metadata.keywords) > MAX_KEYWORDS:
        findings.append(
            Finding(
                code="metadata.too_many_keywords",
                severity=Severity.BLOCKER,
                message=(
                    f"{len(metadata.keywords)} keywords supplied; KDP accepts "
                    f"{MAX_KEYWORDS}."
                ),
                subject="keywords",
            )
        )

    for keyword in metadata.keywords:
        terms = [t for t in keyword.replace(";", ",").split(",") if t.strip()]
        if len(terms) > MAX_TERMS_PER_KEYWORD:
            findings.append(
                Finding(
                    code="metadata.keyword_stuffing",
                    severity=Severity.BLOCKER,
                    message=(
                        f"Keyword slot packs {len(terms)} terms. Keyword "
                        "stuffing is a named removal trigger."
                    ),
                    subject="keywords",
                    detail={"keyword": keyword},
                )
            )

    normalised = [k.strip().lower() for k in metadata.keywords]
    repeats = sorted({k for k in normalised if normalised.count(k) > 1 and k})
    if repeats:
        findings.append(
            Finding(
                code="metadata.duplicate_keywords",
                severity=Severity.MAJOR,
                message=f"Repeated keyword slots: {', '.join(repeats)}.",
                subject="keywords",
            )
        )

    if not metadata.categories:
        findings.append(
            Finding(
                code="metadata.no_categories",
                severity=Severity.MAJOR,
                message="No browse categories chosen.",
                subject="categories",
            )
        )

    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "book_id": manifest.book_id,
            "disclosure": disclosure.to_dict(),
            "disclosure_statement": disclosure.statement(),
            "keyword_count": len(metadata.keywords),
        },
    )
