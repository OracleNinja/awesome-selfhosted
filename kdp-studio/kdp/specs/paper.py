"""Paper stocks and their manufacturing limits.

``caliper_in`` is the thickness of a single *page* (one side of a leaf), which
is what KDP's spine formula multiplies by the page count. Getting this wrong
produces a cover whose spine text drifts onto the front panel, which is one of
the most common rejections for illustrated books.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..errors import SpecError


@dataclass(frozen=True, slots=True)
class Paper:
    """A paper stock offered for KDP paperbacks.

    ``ink`` and ``stock`` are what the printing-cost tables are keyed on. They
    live here so that a book's declared paper resolves to a cost band without
    the caller restating it — restating it is how the two drift apart.
    """

    key: str
    label: str
    #: Thickness of one page, in inches. spine = page_count * caliper_in.
    caliper_in: float
    #: Inclusive page-count bounds for this stock.
    min_pages: int
    max_pages: int
    colour: bool
    #: "black" | "standard_colour" | "premium_colour"
    ink: str = "black"
    #: "white" | "cream" | "groundwood"
    stock: str = "white"

    def spine_width_in(self, page_count: int) -> float:
        """Spine width for ``page_count`` pages on this stock, in inches."""
        if page_count < 0:
            raise SpecError(f"page_count must not be negative: {page_count}")
        return round(page_count * self.caliper_in, 5)

    def supports_page_count(self, page_count: int) -> bool:
        return self.min_pages <= page_count <= self.max_pages


#: Spine calipers verified against KDP's paperback submission guidelines
#: (2026-08-31). The guideline gives one figure for colour paper, so both
#: colour tiers share it: KDP distinguishes standard from premium in *printing
#: cost*, not in paper thickness, and inventing a separate caliper for one of
#: them would put every standard-colour spine at the wrong width.
PAPERS: Final[dict[str, Paper]] = {
    p.key: p
    for p in (
        Paper("bw_white", "Black ink on white paper", 0.002252, 24, 828, False,
              ink="black", stock="white"),
        Paper("bw_cream", "Black ink on cream paper", 0.0025, 24, 828, False,
              ink="black", stock="cream"),
        Paper("bw_groundwood", "Black ink on groundwood paper", 0.00235, 24, 828, False,
              ink="black", stock="groundwood"),
        Paper("premium_colour", "Premium colour on white paper", 0.002347, 24, 828, True,
              ink="premium_colour", stock="white"),
        Paper("standard_colour", "Standard colour on white paper", 0.002347, 72, 600, True,
              ink="standard_colour", stock="white"),
    )
}

#: The default for line-art interiors: black ink, white stock, widest page range.
DEFAULT_PAPER: Final[str] = "bw_white"


def get_paper(key: str) -> Paper:
    try:
        return PAPERS[key]
    except KeyError:
        known = ", ".join(sorted(PAPERS))
        raise SpecError(f"unknown paper {key!r}; known stocks: {known}") from None
