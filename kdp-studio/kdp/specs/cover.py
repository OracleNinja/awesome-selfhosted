"""Full-wrap cover geometry.

A paperback cover is submitted as one PDF page spanning back cover, spine and
front cover, with bleed on all four edges. Every dimension below derives from
the trim size, the page count and the paper stock, so the cover cannot drift
out of agreement with the interior unless someone changes one and not the
other — which is exactly what the preflight gate checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from .interior import BLEED_IN, MIN_PAGES_FOR_SPINE_TEXT
from .paper import Paper
from .trim import TrimSize

#: Clear space required between spine text and either spine fold.
SPINE_TEXT_SAFETY_IN: float = 0.0625

#: Nothing meant to survive trimming may sit closer than this to a cover edge.
COVER_SAFE_MARGIN_IN: float = 0.25


@dataclass(frozen=True, slots=True)
class CoverGeometry:
    """The full-wrap cover canvas and the panels inside it."""

    trim: TrimSize
    paper: Paper
    page_count: int
    spine_width_in: float
    total_width_in: float
    total_height_in: float
    spine_text_allowed: bool

    @property
    def spine_text_width_in(self) -> float:
        """Usable spine width once the safety margins are taken off.

        Zero when the spine is too narrow to hold text safely.
        """
        usable = self.spine_width_in - (2 * SPINE_TEXT_SAFETY_IN)
        return round(usable, 5) if usable > 0 else 0.0

    @property
    def front_panel_origin_x_in(self) -> float:
        """Distance from the left edge of the canvas to the front cover fold."""
        return round(BLEED_IN + self.trim.width_in + self.spine_width_in, 5)

    def to_dict(self) -> dict:
        return {
            "trim": self.trim.key,
            "paper": self.paper.key,
            "page_count": self.page_count,
            "spine_width_in": self.spine_width_in,
            "total_width_in": self.total_width_in,
            "total_height_in": self.total_height_in,
            "spine_text_allowed": self.spine_text_allowed,
            "spine_text_width_in": self.spine_text_width_in,
            "front_panel_origin_x_in": self.front_panel_origin_x_in,
        }


def cover_geometry(trim: TrimSize, paper: Paper, page_count: int) -> CoverGeometry:
    """Resolve the full-wrap cover canvas.

    Width is bleed + back cover + spine + front cover + bleed; height is the
    trim height plus bleed at the top and the bottom.
    """
    spine = paper.spine_width_in(page_count)
    total_width = round((2 * BLEED_IN) + (2 * trim.width_in) + spine, 5)
    total_height = round(trim.height_in + (2 * BLEED_IN), 5)

    return CoverGeometry(
        trim=trim,
        paper=paper,
        page_count=page_count,
        spine_width_in=spine,
        total_width_in=total_width,
        total_height_in=total_height,
        spine_text_allowed=page_count >= MIN_PAGES_FOR_SPINE_TEXT,
    )
