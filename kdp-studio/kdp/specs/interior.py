"""Interior page geometry: bleed, margins and the gutter.

Two rules here are worth stating plainly, because they are where illustrated
books usually fail preflight:

**Bleed is asymmetric.** A bleed page grows by 0.125" on the outer edge only,
but by 0.25" vertically — 0.125" at the top *and* at the bottom. A generator
that adds 0.125" to both dimensions produces a page that is short by an eighth
of an inch, and the error is small enough to survive a careless eyeball.

**The gutter grows with the page count.** A thick book needs more inside margin
or the artwork disappears into the spine. For a coloring book this matters more
than for a novel: a drawing that runs into the gutter cannot be coloured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..errors import SpecError
from .trim import TrimSize

#: Added to the outer edge of a bleed page.
BLEED_IN: Final[float] = 0.125

#: Minimum outside/top/bottom margin, measured from the trim edge.
MIN_OUTER_MARGIN_NO_BLEED_IN: Final[float] = 0.25
MIN_OUTER_MARGIN_WITH_BLEED_IN: Final[float] = 0.375

#: Inside (gutter) margin by page count. Bounds are inclusive; the table is
#: consulted in order and the first band that contains the page count wins.
GUTTER_BANDS: Final[tuple[tuple[int, int, float], ...]] = (
    (24, 150, 0.375),
    (151, 300, 0.5),
    (301, 500, 0.625),
    (501, 700, 0.75),
    (701, 828, 0.875),
)

#: KDP does not print spine text on books of 79 pages or fewer, so 80 is the
#: first page count at which a spine can carry text. Verified against KDP Help;
#: an earlier revision of this file used 100, a conservative guess taken from
#: disagreeing secondary sources, which silently withheld spine text from books
#: between 80 and 99 pages.
SPINE_TEXT_CUTOFF_PAGES: Final[int] = 79
MIN_PAGES_FOR_SPINE_TEXT: Final[int] = SPINE_TEXT_CUTOFF_PAGES + 1

#: Interiors are submitted as PDF; raster artwork should be at least this DPI.
MIN_IMAGE_DPI: Final[int] = 300


def gutter_in(page_count: int) -> float:
    """Minimum inside margin for ``page_count`` pages, in inches."""
    for low, high, value in GUTTER_BANDS:
        if low <= page_count <= high:
            return value
    raise SpecError(
        f"no gutter defined for a {page_count}-page interior; "
        f"supported range is {GUTTER_BANDS[0][0]}-{GUTTER_BANDS[-1][1]} pages"
    )


@dataclass(frozen=True, slots=True)
class InteriorGeometry:
    """Every measurement needed to lay out one interior page."""

    trim: TrimSize
    page_count: int
    bleed: bool
    page_width_in: float
    page_height_in: float
    outer_margin_in: float
    gutter_in: float

    @property
    def live_width_in(self) -> float:
        """Width of the safe area, once gutter and outer margin are removed."""
        return round(self.trim.width_in - self.outer_margin_in - self.gutter_in, 5)

    @property
    def live_height_in(self) -> float:
        """Height of the safe area, once top and bottom margins are removed."""
        return round(self.trim.height_in - (2 * self.outer_margin_in), 5)

    def to_dict(self) -> dict:
        return {
            "trim": self.trim.key,
            "page_count": self.page_count,
            "bleed": self.bleed,
            "page_width_in": self.page_width_in,
            "page_height_in": self.page_height_in,
            "outer_margin_in": self.outer_margin_in,
            "gutter_in": self.gutter_in,
            "live_width_in": self.live_width_in,
            "live_height_in": self.live_height_in,
        }


def interior_geometry(trim: TrimSize, page_count: int, bleed: bool) -> InteriorGeometry:
    """Resolve the full page geometry for an interior.

    ``page_width_in``/``page_height_in`` are the dimensions of the PDF page to
    produce, which equal the trim size for a no-bleed interior and exceed it
    asymmetrically for a bleed interior.
    """
    if bleed:
        width = round(trim.width_in + BLEED_IN, 5)
        height = round(trim.height_in + (2 * BLEED_IN), 5)
        outer = MIN_OUTER_MARGIN_WITH_BLEED_IN
    else:
        width = trim.width_in
        height = trim.height_in
        outer = MIN_OUTER_MARGIN_NO_BLEED_IN

    return InteriorGeometry(
        trim=trim,
        page_count=page_count,
        bleed=bleed,
        page_width_in=width,
        page_height_in=height,
        outer_margin_in=outer,
        gutter_in=gutter_in(page_count),
    )
