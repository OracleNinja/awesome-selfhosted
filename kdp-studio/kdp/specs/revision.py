"""Provenance for the print-specification tables themselves.

KDP changes its manufacturing limits. A pipeline that hard-codes them and
forgets where they came from will one day pass a book against numbers that
stopped being true, and the first sign of it will be a rejected submission.

So the tables carry a revision stamp and a verification state, and the
spec-legality gate refuses to certify a book whose numbers were never checked
against a primary source. ``UNVERIFIED`` is a blocking condition, not a
warning — the safe direction here is to stop.

To verify: open the KDP Help pages for paperback submission guidelines, trim
size / bleed / margins, and paper options, confirm every value in ``paper.py``,
``trim.py`` and ``interior.py``, then set ``VERIFICATION`` to ``VERIFIED`` and
``VERIFIED_ON`` to the date you checked.
"""

from __future__ import annotations

from typing import Final, Literal

#: Bumped whenever any number in this sub-package changes. Recorded into every
#: manifest and every gate result, so an old result can always be explained.
SPEC_REVISION: Final[str] = "2026.08-r1"

VerificationState = Literal["VERIFIED", "UNVERIFIED"]

#: Values were transcribed from public KDP documentation summaries. The
#: authoritative pages were not reachable from the environment this revision
#: was authored in, so nothing here has been confirmed first-hand.
VERIFICATION: Final[VerificationState] = "UNVERIFIED"

#: ISO date the tables were last confirmed against a primary source.
VERIFIED_ON: Final[str | None] = None

#: Points a human at what to go and read. Surfaced verbatim in gate findings.
VERIFICATION_NOTE: Final[str] = (
    "Print specification tables are UNVERIFIED. Confirm trim sizes, paper "
    "caliper, page-count limits and margin rules against KDP Help before "
    "relying on any computed spine width or cover canvas for a real "
    "submission, then update kdp/specs/revision.py."
)


def is_verified() -> bool:
    """True when the tables have been checked against a primary source."""
    return VERIFICATION == "VERIFIED"
