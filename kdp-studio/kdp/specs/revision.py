"""Provenance for the print-specification tables themselves.

KDP changes its manufacturing limits. A pipeline that hard-codes them and
forgets where they came from will one day pass a book against numbers that
stopped being true, and the first sign of it will be a rejected submission.

So every table carries a :class:`SourceRecord` saying where its numbers came
from, when they were checked, and against which marketplace. A table with no
verified source is ``UNVERIFIED``, and the gates that depend on it refuse to
certify a book. That is a deliberate stop, not a warning: a green preflight
computed from unchecked numbers is worse than none, because it is trusted.

To verify a table: read the primary KDP Help page, confirm every value in the
corresponding module, then fill in its :class:`SourceRecord` here with the URL
and the date you checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

#: Bumped whenever any number in this sub-package changes. Recorded into every
#: manifest and every gate result, so an old result can always be explained.
SPEC_REVISION: Final[str] = "2026.08-r3"

VerificationState = Literal["VERIFIED", "UNVERIFIED"]

#: How long a verification stays trusted before it must be re-checked. KDP
#: revises its manufacturing limits without notice, so a check from two years
#: ago is not evidence about today.
VERIFICATION_TTL_DAYS: Final[int] = 180


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Where one specification table's numbers came from.

    ``verified_on`` is the date a human read the primary source and confirmed
    the values. It is not the date the file was edited.
    """

    #: Which table this describes: "trim", "paper", "interior", "cover",
    #: "royalty", "policy".
    table: str
    #: Primary KDP Help URL the values were confirmed against. Secondary
    #: sources — blogs, calculators, summaries — do not count as verification
    #: and must not be recorded here.
    source_url: str | None = None
    #: ISO date the values were last confirmed against ``source_url``.
    verified_on: str | None = None
    #: Marketplace the values apply to. KDP's limits differ by marketplace.
    marketplace: str = "amazon.com"
    #: The version or revision label the source page carried, where it shows
    #: one. Free text; KDP does not version its help pages consistently, so an
    #: access date is often all there is.
    spec_version: str = ""
    #: Where to go and read, when the exact URL is known. Left empty rather
    #: than guessed: a confidently wrong link sends someone to the wrong page
    #: and they verify against it. ``find`` names the page instead.
    expected_url: str = ""
    #: The KDP Help page title to search for, when no URL is recorded.
    find: str = ""
    #: What specifically to confirm on that page.
    confirm: tuple[str, ...] = field(default_factory=tuple)
    #: Free-form note: caveats, values that could not be confirmed, ambiguity.
    notes: str = ""

    @property
    def state(self) -> VerificationState:
        return "VERIFIED" if (self.source_url and self.verified_on) else "UNVERIFIED"

    def is_stale(self, today: date | None = None) -> bool:
        """True when a verification exists but has aged past the TTL.

        An unverified table is not "stale" — it was never fresh. Callers check
        ``state`` for that; this answers a different question.
        """
        if self.state == "UNVERIFIED" or not self.verified_on:
            return False
        try:
            checked = date.fromisoformat(self.verified_on)
        except ValueError:
            # An unparseable date is treated as stale rather than as fresh:
            # a malformed record is not evidence.
            return True
        return (today or date.today()) - checked > _ttl()

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "source_url": self.source_url,
            "verified_on": self.verified_on,
            "marketplace": self.marketplace,
            "spec_version": self.spec_version,
            "expected_url": self.expected_url,
            "find": self.find,
            "confirm": list(self.confirm),
            "state": self.state,
            "notes": self.notes,
        }


def _ttl():
    from datetime import timedelta

    return timedelta(days=VERIFICATION_TTL_DAYS)


#: The values in this package were supplied by an operator who checked them
#: against KDP Help. The *record* still cannot be marked VERIFIED, because
#: verification means a citation someone else can re-check: a URL and a date.
#: Neither was supplied, and neither can be obtained here — every amazon.com
#: host is refused by network policy in this environment.
#:
#: This is the distinction the whole mechanism exists to hold. A value someone
#: believes is not the same as a value someone can point at, and only the
#: second survives the question "where did this come from?" six months later.
_VALUES_SUPPLIED_NOTE = (
    "Values updated from an operator's verification against KDP Help. The "
    "record is still UNVERIFIED because no source URL, verification date or "
    "spec version was supplied — those are what make a verification auditable. "
    "Add source_url and verified_on to flip this table."
)

_UNVERIFIED_NOTE = (
    "Not verified. Every amazon.com host is blocked by network policy in the "
    "environment this package is developed in, so no value here has been "
    "confirmed against a primary source."
)

#: One record per table. Fill in ``source_url``, ``verified_on`` and, where the
#: page shows one, ``spec_version`` after reading the primary page. That alone
#: flips the table to VERIFIED — ``kdp verify-specs`` prints the checklist.
SOURCES: Final[dict[str, SourceRecord]] = {
    record.table: record
    for record in (
        SourceRecord(
            "trim",
            expected_url="https://kdp.amazon.com/en_US/help/topic/G201857950",
            confirm=(
                "the complete list of trim sizes offered, and which are "
                "standard — the 15 entries here are NOT known to be complete",
                "which sizes are available for expanded distribution",
                "which sizes KDP prices as regular trim and which as large; "
                "economics cannot price a book without this",
            ),
            notes=_UNVERIFIED_NOTE
            + " Values live in kdp/specs/trim.py. This table has NOT been "
            "reconciled against KDP's current trim-size list — the list was not "
            "supplied — so the 15 entries may be incomplete or out of date. It "
            "also carries no cost class per size (regular vs large trim), which "
            "is what economics needs to price a book from its trim alone.",
        ),
        SourceRecord(
            "paper",
            find="KDP Help > Print Options (paper type, ink, page count limits)",
            confirm=(
                "paper stocks offered and their per-page caliper",
                "minimum and maximum page count for each stock",
            ),
            notes=_VALUES_SUPPLIED_NOTE
            + " Spine coefficients corrected: standard colour moved from "
            "0.002347 to 0.002252, matching black ink. Values live in "
            "kdp/specs/paper.py. Page-count limits were NOT re-verified.",
        ),
        SourceRecord(
            "interior",
            expected_url="https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6",
            confirm=(
                "bleed dimensions, and that a bleed page grows 0.125 in in "
                "width but 0.25 in in height",
                "minimum outside/top/bottom margins with and without bleed",
                "the inside (gutter) margin bands by page count",
            ),
            notes=_VALUES_SUPPLIED_NOTE
            + " Bleed, margins and gutter bands were confirmed unchanged. "
            "Values live in kdp/specs/interior.py.",
        ),
        SourceRecord(
            "cover",
            find="KDP Help > Format Your Paperback Cover / Cover Requirements",
            confirm=(
                "the full-wrap cover formula and its bleed allowance",
                "the spine width calculation",
                "the page count at or above which spine text is printed",
            ),
            notes=_VALUES_SUPPLIED_NOTE
            + " The contested spine-text threshold is resolved: KDP prints no "
            "spine text at 79 pages or fewer, so 80 is the first eligible page "
            "count. The previous value of 100 was a conservative guess that "
            "silently withheld spine text from books of 80 to 99 pages. Values "
            "live in kdp/specs/interior.py and kdp/specs/cover.py.",
        ),
        SourceRecord(
            "royalty",
            find="KDP Help > Printing Cost & Royalty Calculation (paperback)",
            confirm=(
                "the paperback royalty rate for this marketplace",
                "the printing-cost formula: fixed charge plus per-page charge",
            ),
            notes=_VALUES_SUPPLIED_NOTE
            + " The model is now banded rather than formulaic: royalty is 50% "
            "below $9.99 and 60% at or above on amazon.com, and black-ink "
            "regular-trim printing is a flat $2.30 to 110 pages then $1.00 + "
            "$0.012/page. Only that one marketplace/ink/trim combination is "
            "held; every other raises rather than extrapolating. Projections "
            "still report at UNKNOWN confidence because the trim table carries "
            "no cost class, so no book can be mapped to a cost band from its "
            "trim alone.",
        ),
        SourceRecord(
            "policy",
            find="KDP Help > Content Guidelines, and the AI-generated content page",
            confirm=(
                "the AI-generated content disclosure wording, and the "
                "generated/assisted distinction",
                "the low-content policy, and that coloring books are listed "
                "under 'Not Generally Low-Content'",
                "the duplicative-content policy, which applies to every title",
                "the limit on new titles per 24 hours",
            ),
            notes=_UNVERIFIED_NOTE
            + " Drives G12. Cross-check the disclosure wording quoted in "
            "COMPLIANCE.md against the live page.",
        ),
    )
}


def _wrap(text: str, first: str, rest: str, width: int = 78) -> list[str]:
    import textwrap

    return textwrap.wrap(
        text, width=width, initial_indent=first, subsequent_indent=rest
    )


def checklist() -> str:
    """What a human needs to do to verify the tables, as readable text.

    Exists because the verification cannot be automated from inside this
    package — the pages are not reachable here — and a blocker with no
    instructions is just an obstacle.
    """
    lines = [
        "KDP specification verification checklist",
        "=" * 40,
        "",
        "Each table below is UNVERIFIED until a human reads the primary KDP",
        "Help page and confirms the values. Secondary sources — blogs,",
        "calculators, summaries — do not count and must not be recorded.",
        "",
        "Where a URL is shown it was seen in a search result and is a starting",
        "point, not a guarantee: confirm the page you land on is the one named.",
        "Where only a page title is shown, no URL is recorded because none was",
        "confirmed, and guessing one would send you to the wrong page.",
        "",
        f"Verifications expire after {VERIFICATION_TTL_DAYS} days.",
        "",
    ]
    for name in sorted(SOURCES):
        record = SOURCES[name]
        lines.append(f"[{record.state}] {name}")
        if record.expected_url:
            lines.append(f"  read:    {record.expected_url}")
        else:
            # No URL is recorded rather than a guessed one: being sent to the
            # wrong page is worse than being asked to find the right one.
            lines.append(f"  find:    {record.find or '(page not identified)'}")
        for item in record.confirm:
            lines.append(f"  confirm: {item}")
        if record.state == "VERIFIED":
            lines.append(f"  checked: {record.verified_on} ({record.marketplace})")
            if record.spec_version:
                lines.append(f"  version: {record.spec_version}")
            if record.is_stale():
                lines.append("  STALE — re-check; the values may have moved.")
        if record.notes:
            for line in _wrap(record.notes, "  note:    ", "           "):
                lines.append(line)
        lines.append("")

    lines += [
        "To record a verification, edit kdp/specs/revision.py and set on that",
        "table's SourceRecord:",
        "",
        '    source_url="<the page you actually read>",',
        '    verified_on="YYYY-MM-DD",',
        '    spec_version="<any revision label the page shows>",',
        '    marketplace="amazon.com",',
        "",
        "Then bump SPEC_REVISION and run the test suite: the geometry tests",
        "assert the arithmetic, so a changed constant shows up immediately.",
    ]
    return "\n".join(lines)


def source(table: str) -> SourceRecord:
    """The source record for one table. Unknown tables are unverified."""
    return SOURCES.get(table, SourceRecord(table, notes="No source record defined."))


def unverified_tables() -> tuple[str, ...]:
    return tuple(sorted(t for t, r in SOURCES.items() if r.state == "UNVERIFIED"))


def stale_tables(today: date | None = None) -> tuple[str, ...]:
    return tuple(sorted(t for t, r in SOURCES.items() if r.is_stale(today)))


def is_verified(*tables: str, today: date | None = None) -> bool:
    """True when every named table is verified and not stale.

    With no arguments, asks about every table.
    """
    names = tables or tuple(SOURCES)
    return all(
        source(t).state == "VERIFIED" and not source(t).is_stale(today) for t in names
    )


def verification_problem(*tables: str, today: date | None = None) -> str | None:
    """A sentence naming what is wrong, or None when everything checks out.

    Returned verbatim in gate findings, so it has to be readable by someone
    who has never opened this file.
    """
    names = tables or tuple(SOURCES)
    unverified = [t for t in names if source(t).state == "UNVERIFIED"]
    stale = [t for t in names if source(t).is_stale(today)]
    if not unverified and not stale:
        return None

    parts = []
    if unverified:
        parts.append(f"never verified: {', '.join(sorted(unverified))}")
    if stale:
        parts.append(
            f"verified more than {VERIFICATION_TTL_DAYS} days ago: "
            f"{', '.join(sorted(stale))}"
        )
    return (
        f"KDP specification tables cannot be trusted ({'; '.join(parts)}). "
        "Confirm the values against the primary KDP Help pages and record the "
        "URL and date in kdp/specs/revision.py before certifying any book."
    )


#: Back-compat aliases. The single-state constants predate per-table sources;
#: they now summarise the whole set.
VERIFICATION: Final[VerificationState] = "VERIFIED" if is_verified() else "UNVERIFIED"
VERIFIED_ON: Final[str | None] = None
VERIFICATION_NOTE: Final[str] = (
    verification_problem() or "All KDP specification tables are verified."
)
