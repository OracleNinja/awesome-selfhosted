"""Tatami / scanline fill generation.

The classic commercial fill: parallel rows of running stitch at a chosen angle,
with the needle penetrations staggered row to row so the design does not
develop a visible "split line" down the middle.

The row-grouping step is what separates a usable fill from a jump-stitch mess.
Rows are cut into segments by the polygon boundary, segments in adjacent rows
that overlap are unioned into a *section*, and each section is sewn
boustrophedon before travelling to the next.  A doughnut or a letter "E" then
sews as a handful of clean passes instead of hundreds of jumps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Polygon

from .geometry import Point, clip_line, distance, rotate_point


@dataclass(slots=True)
class FillParams:
    spacing: float               # distance between rows, internal units
    angle_deg: float = 45.0
    max_stitch: float = 120.0    # 12 mm
    min_stitch: float = 6.0      # 0.6 mm
    stagger: int = 4             # rows before the stagger pattern repeats
    travel_threshold: float = 60.0   # connect sections with a stitch under 6 mm
    underpath: bool = True       # travel inside the shape instead of jumping


@dataclass(slots=True)
class _Segment:
    row: int
    y: float
    x0: float
    x1: float

    def overlaps(self, other: "_Segment", slack: float = 0.0) -> bool:
        return not (self.x1 + slack < other.x0 or other.x1 + slack < self.x0)


def _scan_rows(polygon: Polygon, spacing: float) -> list[_Segment]:
    min_x, min_y, max_x, max_y = polygon.bounds
    if spacing <= 0:
        spacing = 4.0
    pad = max(spacing, 10.0)
    segments: list[_Segment] = []
    row = 0
    y = min_y + spacing / 2.0
    while y <= max_y:
        line = LineString([(min_x - pad, y), (max_x + pad, y)])
        for piece in clip_line(polygon, line):
            xs = [p[0] for p in piece]
            x0, x1 = min(xs), max(xs)
            if x1 - x0 > 1e-6:
                segments.append(_Segment(row=row, y=y, x0=x0, x1=x1))
        row += 1
        y += spacing
    return segments


def _group_sections(segments: list[_Segment]) -> list[list[_Segment]]:
    """Union-find over segments that overlap between consecutive rows."""
    parent = list(range(len(segments)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_row: dict[int, list[int]] = {}
    for i, seg in enumerate(segments):
        by_row.setdefault(seg.row, []).append(i)

    for row, indices in by_row.items():
        for j in by_row.get(row + 1, []):
            for i in indices:
                if segments[i].overlaps(segments[j]):
                    union(i, j)

    groups: dict[int, list[_Segment]] = {}
    for i, seg in enumerate(segments):
        groups.setdefault(find(i), []).append(seg)

    sections = [sorted(g, key=lambda s: (s.row, s.x0)) for g in groups.values()]
    sections.sort(key=lambda g: (g[0].row, g[0].x0))
    return sections


def _stitch_run(
    start: Point,
    end: Point,
    max_stitch: float,
    min_stitch: float,
    phase: float = 0.0,
) -> list[Point]:
    """Break a straight run into machine-legal stitches.

    ``phase`` shifts the first penetration so neighbouring rows do not line up
    (the stagger that makes tatami look like tatami).
    """
    length = distance(start, end)
    if length <= 1e-6:
        return [end]
    ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    points: list[Point] = []
    first = phase if 0 < phase < length else max_stitch
    pos = min(first, max_stitch)
    while pos < length - min_stitch:
        points.append((start[0] + ux * pos, start[1] + uy * pos))
        pos += max_stitch
    points.append(end)
    return points


def generate_fill(
    polygon: Polygon,
    params: FillParams,
) -> list[list[Point]]:
    """Return a list of stitch runs; a new run means the machine must travel.

    Coordinates come back in the original (unrotated) frame.
    """
    if polygon.is_empty or polygon.area <= 0:
        return []

    angle = math.radians(params.angle_deg)
    centroid = (polygon.centroid.x, polygon.centroid.y)

    # Work in a rotated frame where rows are horizontal.
    rotated = Polygon(
        [rotate_point(p, -angle, centroid) for p in polygon.exterior.coords],
        [[rotate_point(p, -angle, centroid) for p in ring.coords] for ring in polygon.interiors],
    )
    if not rotated.is_valid:
        rotated = rotated.buffer(0)
        if rotated.is_empty:
            return []
        if rotated.geom_type == "MultiPolygon":
            rotated = max(rotated.geoms, key=lambda g: g.area)

    segments = _scan_rows(rotated, params.spacing)
    if not segments:
        return []

    sections = _group_sections(segments)
    stagger = max(1, params.stagger)
    runs: list[list[Point]] = []

    for section in sections:
        rows: dict[int, list[_Segment]] = {}
        for seg in section:
            rows.setdefault(seg.row, []).append(seg)

        current: list[Point] = []
        left_to_right = True
        prev_point: Point | None = None

        for row_index in sorted(rows):
            row_segments = sorted(rows[row_index], key=lambda s: s.x0)
            if not left_to_right:
                row_segments = list(reversed(row_segments))

            for seg in row_segments:
                start = (seg.x0, seg.y) if left_to_right else (seg.x1, seg.y)
                end = (seg.x1, seg.y) if left_to_right else (seg.x0, seg.y)

                if prev_point is not None:
                    gap = distance(prev_point, start)
                    if gap > params.travel_threshold:
                        if current:
                            runs.append(current)
                        current = []
                    elif gap > params.min_stitch:
                        current.extend(
                            _stitch_run(prev_point, start, params.max_stitch, params.min_stitch)
                        )

                if not current:
                    current.append(start)
                phase = (row_index % stagger) / stagger * params.max_stitch
                current.extend(
                    _stitch_run(start, end, params.max_stitch, params.min_stitch, phase)
                )
                prev_point = end

            left_to_right = not left_to_right

        if current:
            runs.append(current)

    # Rotate everything back into the caller's frame.
    return [[rotate_point(p, angle, centroid) for p in run] for run in runs if len(run) > 1]


def estimate_fill_stitches(polygon: Polygon, spacing: float, max_stitch: float) -> int:
    """Fast analytic estimate used by the quoting engine before digitizing."""
    if polygon.is_empty or spacing <= 0 or max_stitch <= 0:
        return 0
    row_count = max(1.0, polygon.bounds[3] - polygon.bounds[1]) / spacing
    avg_row_length = polygon.area / max(1.0, (polygon.bounds[3] - polygon.bounds[1]))
    per_row = max(1.0, avg_row_length / max_stitch) + 1.0
    return int(row_count * per_row)
