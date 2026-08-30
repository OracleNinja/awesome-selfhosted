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
SPEC_REVISION: Final[str] = "2026.08-r2"

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
    #: Primary KDP Help URL. Secondary sources do not count as verification.
    source_url: str | None = None
    #: ISO date the values were last confirmed against ``source_url``.
    verified_on: str | None = None
    #: Marketplace the values apply to. KDP's limits differ by marketplace.
    marketplace: str = "amazon.com"
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
            "state": self.state,
            "notes": self.notes,
        }


def _ttl():
    from datetime import timedelta

    return timedelta(days=VERIFICATION_TTL_DAYS)


_UNVERIFIED_NOTE = (
    "Transcribed from public secondary summaries. kdp.amazon.com was "
    "unreachable from the environment this revision was authored in, so no "
    "value here has been confirmed against a primary source."
)

#: One record per table. Fill in ``source_url`` and ``verified_on`` after
#: checking the primary page; that alone flips the table to VERIFIED.
SOURCES: Final[dict[str, SourceRecord]] = {
    record.table: record
    for record in (
        SourceRecord(
            "trim",
            notes=_UNVERIFIED_NOTE + " Verify at KDP Help > Paperback Submission "
            "Guidelines > Trim Size.",
        ),
        SourceRecord(
            "paper",
            notes=_UNVERIFIED_NOTE + " Verify caliper and page-count limits at "
            "KDP Help > Paperback Paper & Ink Options.",
        ),
        SourceRecord(
            "interior",
            notes=_UNVERIFIED_NOTE + " Verify bleed, margins and gutter bands at "
            "KDP Help > Set Trim Size, Bleed, and Margins.",
        ),
        SourceRecord(
            "cover",
            notes=_UNVERIFIED_NOTE + " Verify spine calculation and the "
            "page-count threshold for spine text at KDP Help > Cover "
            "Requirements. Secondary sources disagree on the threshold (79 vs "
            "100 pages); 100 is encoded as the conservative choice.",
        ),
        SourceRecord(
            "royalty",
            notes=_UNVERIFIED_NOTE + " Verify the royalty rate and printing-cost "
            "formula at KDP Help > Paperback Printing Cost and Royalty.",
        ),
        SourceRecord(
            "policy",
            notes=_UNVERIFIED_NOTE + " Verify AI-content disclosure wording, "
            "low-content policy and the daily title limit at KDP Help > Content "
            "Guidelines.",
        ),
    )
}


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
