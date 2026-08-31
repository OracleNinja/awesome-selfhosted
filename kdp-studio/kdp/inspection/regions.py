"""Closed-region analysis: can this page actually be coloured in?

A coloring page is a set of *enclosed* areas. Colour one and the fill stops at
a stroke. If an outline has a gap, the fill escapes into the surrounding page —
"leaking" — and the page is unusable even though it looks fine on screen.

The analysis is a connected-component labelling of the non-ink pixels. Anything
reachable from the page border is outside the drawing; anything else is an
enclosed region, which is what a person colours. A drawing with strokes but no
enclosed area is one whose outlines are all open.

**Run-length, not per-pixel.** Line art is mostly long uninterrupted spans of
paper, so each row reduces to a handful of runs — under three on average for a
full-page drawing. Labelling runs instead of pixels makes exact analysis at
print resolution cost about ten milliseconds, which is why nothing here is
approximated or downsampled for speed.

**What it cannot do.** Deciding that one *particular* outline has a gap needs
the drawing segmented into shapes, which needs to know what the shapes are.
This reports totals, plus a gap-closing comparison that indicates small breaks
exist somewhere. That is a signal for a human, and the finding says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Byte markers used to turn a luminance row into something the regex engine
#: can scan at C speed.
_INK = 35    # '#'
_PAPER = 46  # '.'

_PAPER_RUN = re.compile(rb"\.+")


def _table(ink_threshold: int) -> bytes:
    return bytes(_INK if v <= ink_threshold else _PAPER for v in range(256))


@dataclass(frozen=True, slots=True)
class RegionStats:
    """What the connected-component pass found."""

    #: Fraction of the page enclosed by strokes — the colourable area.
    enclosed_fraction: float
    #: How many distinct enclosed areas there are.
    region_count: int
    #: The same fraction after small gaps are bridged. Materially larger than
    #: ``enclosed_fraction`` means outlines have breaks in them.
    closed_enclosed_fraction: float
    #: Pixels in the largest enclosed region, as a fraction of the page.
    largest_region_fraction: float

    @property
    def leak_ratio(self) -> float:
        """How much colourable area bridging small gaps would recover.

        1.0 means bridging changed nothing: every outline is already closed.
        Large values mean a lot of area is currently leaking out through gaps.
        """
        if self.enclosed_fraction <= 0:
            return float("inf") if self.closed_enclosed_fraction > 0 else 1.0
        return self.closed_enclosed_fraction / self.enclosed_fraction

    def to_dict(self) -> dict:
        return {
            "enclosed_fraction": round(self.enclosed_fraction, 5),
            "region_count": self.region_count,
            "closed_enclosed_fraction": round(self.closed_enclosed_fraction, 5),
            "largest_region_fraction": round(self.largest_region_fraction, 5),
            "leak_ratio": (
                None if self.leak_ratio == float("inf") else round(self.leak_ratio, 3)
            ),
        }


class _Union:
    """Union-find over run labels."""

    __slots__ = ("parent",)

    def __init__(self) -> None:
        self.parent: list[int] = []

    def add(self) -> int:
        self.parent.append(len(self.parent))
        return len(self.parent) - 1

    def find(self, a: int) -> int:
        root = a
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[a] != root:  # path compression
            self.parent[a], a = root, self.parent[a]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _label(
    rows: list[bytes] | list[bytearray], width: int, height: int, ink_threshold: int
) -> RegionStats | None:
    """Label paper regions and measure the enclosed ones.

    Returns None for a page with no paper at all — a solid block of ink has no
    regions to speak of, and the ink-coverage check owns that case.
    """
    table = _table(ink_threshold)
    union = _Union()

    #: Per row: list of (start, end_exclusive, label).
    previous: list[tuple[int, int, int]] = []
    labels_by_row: list[list[tuple[int, int, int]]] = []
    touches_border: set[int] = set()

    for y in range(height):
        marked = bytes(rows[y]).translate(table)
        current: list[tuple[int, int, int]] = []

        for match in _PAPER_RUN.finditer(marked):
            start, end = match.span()
            label = union.add()

            # A run on the top or bottom row, or touching a side, is outside.
            if y == 0 or y == height - 1 or start == 0 or end == width:
                touches_border.add(label)

            # Join to every overlapping run in the row above. Runs are sorted,
            # so this is a merge rather than a scan of the whole previous row.
            for p_start, p_end, p_label in previous:
                if p_end <= start:
                    continue
                if p_start >= end:
                    break
                union.union(label, p_label)

            current.append((start, end, label))

        labels_by_row.append(current)
        previous = current

    if not union.parent:
        return None

    # Resolve, then total the area of every component that never touched an edge.
    outside_roots = {union.find(label) for label in touches_border}
    area: dict[int, int] = {}
    for row in labels_by_row:
        for start, end, label in row:
            root = union.find(label)
            if root in outside_roots:
                continue
            area[root] = area.get(root, 0) + (end - start)

    total = width * height
    enclosed = sum(area.values())
    largest = max(area.values()) if area else 0
    return RegionStats(
        enclosed_fraction=enclosed / total,
        region_count=len(area),
        # Filled in by analyse(); this pass measures one mask.
        closed_enclosed_fraction=enclosed / total,
        largest_region_fraction=largest / total,
    )


def _gap_closed_mask(
    rows: list[bytes] | list[bytearray],
    width: int,
    height: int,
    ink_threshold: int,
    factor: int,
) -> tuple[list[bytearray], int, int]:
    """Shrink the page, marking a cell as ink if *any* pixel in it is ink.

    Thickening the strokes this way bridges breaks narrower than a cell. It
    also, unavoidably, closes genuinely narrow passages between shapes — which
    is exactly why a difference between this and the raw pass is reported as a
    signal for a human rather than as a verdict.
    """
    out_w = max(1, width // factor)
    out_h = max(1, height // factor)
    small = [bytearray([255]) * out_w for _ in range(out_h)]

    table = _table(ink_threshold)
    ink_run = re.compile(rb"#+")

    for y in range(height):
        target = small[min(y // factor, out_h - 1)]
        # Scan the ink spans rather than the paper ones: ink is the minority on
        # a line-art page, so this touches far fewer cells.
        for match in ink_run.finditer(bytes(rows[y]).translate(table)):
            start, end = match.span()
            for cell in range(start // factor, min(end // factor + 1, out_w)):
                target[cell] = 0
    return small, out_w, out_h


def analyse(
    rows: list[bytes] | list[bytearray],
    width: int,
    height: int,
    ink_threshold: int,
) -> RegionStats | None:
    """Measure enclosed area, and how much of it small gaps are costing."""
    raw = _label(rows, width, height, ink_threshold)
    if raw is None:
        return None

    # Bridge breaks up to roughly a quarter of a percent of the short side —
    # a few pixels at print resolution, which is the scale of gap that a
    # generator leaves and a person would not notice on screen.
    factor = max(2, min(width, height) // 400)
    small, out_w, out_h = _gap_closed_mask(rows, width, height, ink_threshold, factor)
    closed = _label(small, out_w, out_h, ink_threshold)

    return RegionStats(
        enclosed_fraction=raw.enclosed_fraction,
        region_count=raw.region_count,
        closed_enclosed_fraction=(
            closed.enclosed_fraction if closed else raw.enclosed_fraction
        ),
        largest_region_fraction=raw.largest_region_fraction,
    )
