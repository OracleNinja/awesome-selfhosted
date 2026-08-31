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
SPEC_REVISION: Final[str] = "2026.08-r4"

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
    #: Further official pages supporting the same table, where more than one
    #: page was needed.
    supporting_urls: tuple[str, ...] = field(default_factory=tuple)
    #: Questions the source itself left open. A table with any of these is
    #: NOT verified however good its citation: a source that contradicts itself
    #: has been read, not resolved, and the difference matters at the point
    #: someone relies on the number.
    open_questions: tuple[str, ...] = field(default_factory=tuple)
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
        if self.open_questions:
            return "UNVERIFIED"
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
            "supporting_urls": list(self.supporting_urls),
            "open_questions": list(self.open_questions),
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
#: Verified against official KDP Help pages on 2026-08-31 for Amazon.com/US.
#:
#: Five tables are verified. ``royalty`` is not, and deliberately so: KDP's own
#: printing-cost table labels adjacent page bands with the same boundary, so at
#: exactly 110 pages two different prices are published. The page was read; the
#: contradiction was not resolved. Recording that as verified would convert a
#: known discrepancy into a silent tie-break.
_VERIFIED_ON: Final[str] = "2026-08-31"
_MARKETPLACE: Final[str] = "Amazon.com / United States"
_SPEC_VERSION: Final[str] = "KDP Help en_US, retrieved 2026-08-31"

SOURCES: Final[dict[str, SourceRecord]] = {
    record.table: record
    for record in (
        SourceRecord(
            "interior",
            source_url="https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6",
            verified_on=_VERIFIED_ON,
            marketplace=_MARKETPLACE,
            spec_version=_SPEC_VERSION,
            confirm=(
                "bleed 0.125in, applied to the outer edge in width and to both "
                "top and bottom in height",
                "outside margin at least 0.25in without bleed, 0.375in with",
                "gutter bands 0.375 / 0.5 / 0.625 / 0.75 / 0.875in by page count",
            ),
            notes="All values confirmed unchanged. Live in kdp/specs/interior.py.",
        ),
        SourceRecord(
            "trim",
            source_url="https://kdp.amazon.com/en_US/help/topic/G201857950",
            verified_on=_VERIFIED_ON,
            marketplace=_MARKETPLACE,
            spec_version=_SPEC_VERSION,
            confirm=(
                "the 16 US paperback trim sizes",
                "large trim is over 6.12in wide or over 9in high",
            ),
            notes=(
                "Reconciled: 8.25x8.25 was missing and has been added, making "
                "16 sizes. Cost class is derived from KDP's own rule rather "
                "than transcribed per size, and the derivation is asserted "
                "against the published regular/large split in tests. 8.5x11 is "
                "LARGE. Expanded-distribution eligibility (the 'standard' flag) "
                "was NOT part of this check and is None for unverified sizes."
            ),
        ),
        SourceRecord(
            "paper",
            source_url="https://kdp.amazon.com/en_US/help/topic/G201857950",
            verified_on=_VERIFIED_ON,
            marketplace=_MARKETPLACE,
            spec_version=_SPEC_VERSION,
            confirm=(
                "spine caliper per page: white 0.002252, cream 0.0025, "
                "groundwood 0.00235, colour 0.002347",
            ),
            notes=(
                "Groundwood added. Both colour tiers share 0.002347: the "
                "submission guideline gives one colour caliper, and KDP "
                "separates standard from premium in printing cost, not paper "
                "thickness. A revision in between briefly carried 0.002252 for "
                "standard colour on an earlier instruction; that is superseded. "
                "Per-stock page-count limits were NOT separately verified — "
                "they are taken from the ranges the printing-cost table prices, "
                "so a book outside them fails at pricing regardless."
            ),
        ),
        SourceRecord(
            "cover",
            source_url="https://kdp.amazon.com/en_US/help/topic/G201857950",
            verified_on=_VERIFIED_ON,
            marketplace=_MARKETPLACE,
            spec_version=_SPEC_VERSION,
            confirm=(
                "cover bleed 0.125in and safe margin at least 0.25in",
                "spine text only above 79 pages",
                "spine text safety 0.0625in each side",
            ),
            notes=(
                "The contested threshold is settled: spine text requires more "
                "than 79 pages, so 80 is the first eligible count. An earlier "
                "revision guessed 100 and silently withheld spine text from "
                "books of 80 to 99 pages."
            ),
        ),
        SourceRecord(
            "royalty",
            source_url="https://kdp.amazon.com/en_US/help/topic/G201834330",
            supporting_urls=(
                "https://kdp.amazon.com/en_US/help/topic/G201834340",
            ),
            verified_on=_VERIFIED_ON,
            marketplace=_MARKETPLACE,
            spec_version=_SPEC_VERSION,
            confirm=(
                "royalty 50% at $9.98 or below, 60% at $9.99 or above",
                "royalty = rate x list price - printing cost",
                "printing cost bands by ink, paper, trim class and page count",
            ),
            open_questions=(
                "KDP's printing-cost table labels the black-ink bands 24-110 "
                "AND 110-828, so exactly 110 pages carries two published "
                "prices ($2.30/$2.84 flat, or $1.00 + per-page). Which band "
                "KDP actually applies at 110 is unresolved. The model refuses "
                "to price a 110-page black-ink book rather than choosing.",
                "Groundwood bands jump 112 to 114 and premium colour jumps 40 "
                "to 42, leaving 113 and 41 pages with no published price. The "
                "model refuses those too.",
            ),
            notes=(
                "Ten cost tables recorded, keyed by marketplace, ink, paper "
                "stock and trim class. Nothing is interpolated. Rate and cost "
                "bands are otherwise confirmed; this table stays UNVERIFIED "
                "solely because of the boundary questions above."
            ),
        ),
        SourceRecord(
            "policy",
            source_url="https://kdp.amazon.com/en_US/help/topic/G200672390/",
            supporting_urls=(
                "https://kdp.amazon.com/en_US/help/topic/GGE5T76TWKA85DJM",
            ),
            verified_on=_VERIFIED_ON,
            marketplace=_MARKETPLACE,
            spec_version=_SPEC_VERSION,
            confirm=(
                "AI-generated text, images and translations must be disclosed",
                "AI-assisted content need not be disclosed",
                "AI-generated cover and interior images count as AI-generated",
                "coloring books are listed under 'Not Generally Low-Content'",
            ),
            notes=(
                "The publisher remains responsible for IP and content "
                "compliance regardless of how content was produced — no "
                "automated check in this package discharges that. The daily "
                "new-title limit was NOT part of this check; the submission "
                "checklist still cites three per 24 hours from a secondary "
                "source."
            ),
        ),
    )
}


def _wrap(text: str, first: str, rest: str, width: int = 78) -> list[str]:
    import textwrap

    return textwrap.wrap(
        text, width=width, initial_indent=first, subsequent_indent=rest
    )


def checklist() -> str:
    """What still needs doing, as readable text.

    The verification itself happens outside this package — the pages are not
    reachable from it — so a blocker with no instructions would just be an
    obstacle.
    """
    lines = [
        "KDP specification verification checklist",
        "=" * 40,
        "",
        "A table is VERIFIED when an official KDP Help page was read, its URL",
        "and date recorded, and the source left no open questions. Secondary",
        "sources — blogs, calculators, summaries — do not count.",
        "",
        "A table marked UNVERIFIED with a source recorded has been read but not",
        "settled: the page itself is ambiguous. Those are listed as OPEN below.",
        "",
        f"Verifications expire after {VERIFICATION_TTL_DAYS} days.",
        "",
    ]
    for name in sorted(SOURCES):
        record = SOURCES[name]
        lines.append(f"[{record.state}] {name}")
        if record.source_url:
            lines.append(f"  source:  {record.source_url}")
        elif record.expected_url:
            lines.append(f"  read:    {record.expected_url}")
        else:
            lines.append(f"  find:    {record.find or '(page not identified)'}")
        for url in record.supporting_urls:
            lines.append(f"  also:    {url}")
        if record.verified_on:
            lines.append(f"  checked: {record.verified_on} ({record.marketplace})")
        if record.spec_version:
            lines.append(f"  version: {record.spec_version}")
        if record.is_stale():
            lines.append("  STALE — re-check; the values may have moved.")
        for item in record.confirm:
            for line in _wrap(item, "  confirm: ", "           "):
                lines.append(line)
        for question in record.open_questions:
            for line in _wrap(question, "  OPEN:    ", "           "):
                lines.append(line)
        if record.notes:
            for line in _wrap(record.notes, "  note:    ", "           "):
                lines.append(line)
        lines.append("")

    lines += [
        "To record a verification, edit kdp/specs/revision.py and set on that",
        "table's SourceRecord: source_url, verified_on, spec_version and",
        "marketplace. Clear open_questions only once the source's own ambiguity",
        "has actually been resolved.",
        "",
        "Then bump SPEC_REVISION and run the test suite: the geometry and",
        "economics tests assert the arithmetic, so a changed constant shows up",
        "immediately.",
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
    unsourced = [
        t for t in names
        if source(t).state == "UNVERIFIED" and not source(t).open_questions
    ]
    unresolved = [t for t in names if source(t).open_questions]
    stale = [t for t in names if source(t).is_stale(today)]
    if not unsourced and not unresolved and not stale:
        return None

    parts = []
    if unsourced:
        parts.append(f"never verified: {', '.join(sorted(unsourced))}")
    if unresolved:
        # Read but not settled. Saying "never verified" here would be wrong and
        # would send someone to re-read a page they have already read.
        detail = "; ".join(
            f"{t} — {q}"
            for t in sorted(unresolved)
            for q in source(t).open_questions
        )
        parts.append(f"sourced but unresolved ({detail})")
    if stale:
        parts.append(
            f"verified more than {VERIFICATION_TTL_DAYS} days ago: "
            f"{', '.join(sorted(stale))}"
        )
    return (
        f"KDP specification tables cannot be relied on ({'. '.join(parts)}). "
        "Record a source URL and date in kdp/specs/revision.py for anything "
        "never verified, and resolve the open questions for anything sourced "
        "but unresolved, before certifying a book."
    )


#: Back-compat aliases. The single-state constants predate per-table sources;
#: they now summarise the whole set.
VERIFICATION: Final[VerificationState] = "VERIFIED" if is_verified() else "UNVERIFIED"
VERIFIED_ON: Final[str | None] = None
VERIFICATION_NOTE: Final[str] = (
    verification_problem() or "All KDP specification tables are verified."
)
