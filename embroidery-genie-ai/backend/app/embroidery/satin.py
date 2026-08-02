"""Satin column generation.

A satin column is defined by two *rails*.  The needle walks up one rail and
down the other, laying a dense zigzag whose sheen is what makes lettering and
borders look professional.

Two entry points:

``satin_from_centerline``  — a stroke path plus a width (text, outlines).
``satin_from_ring``        — a narrow closed contour, split into two rails
                             along its long axis (traced logos, letterforms).

Both apply pull compensation by widening the column perpendicular to the
stitch direction: thread tension pulls fabric inward as it sews, so a 3 mm
column digitised at exactly 3 mm finishes narrower than drawn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Point, Ring, dedupe, distance, resample_path


@dataclass(slots=True)
class SatinParams:
    spacing: float               # gap between zigzag legs, internal units
    pull_compensation: float = 2.0
    max_width: float = 100.0     # split into fill above this (10 mm)
    min_width: float = 4.0       # below 0.4 mm a satin is really a run
    split_stitch: float = 100.0  # split legs longer than 10 mm
    density_ramp: bool = True    # tighten spacing on the inside of curves


def _normals(path: list[Point]) -> list[tuple[float, float]]:
    """Unit normal at every point of a polyline (averaged across the vertex)."""
    out: list[tuple[float, float]] = []
    n = len(path)
    for i in range(n):
        if i == 0:
            ax, ay = path[0]
            bx, by = path[1]
        elif i == n - 1:
            ax, ay = path[n - 2]
            bx, by = path[n - 1]
        else:
            ax, ay = path[i - 1]
            bx, by = path[i + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            out.append((0.0, 0.0))
        else:
            out.append((-dy / length, dx / length))
    return out


def rails_from_centerline(
    centerline: list[Point], width: float, taper: list[float] | None = None
) -> tuple[list[Point], list[Point]]:
    """Offset a path to both sides to build the two rails of a column."""
    path = dedupe(centerline, 0.5)
    if len(path) < 2:
        return ([], [])
    normals = _normals(path)
    left: list[Point] = []
    right: list[Point] = []
    for i, (px, py) in enumerate(path):
        half = (width * (taper[i] if taper and i < len(taper) else 1.0)) / 2.0
        nx, ny = normals[i]
        left.append((px + nx * half, py + ny * half))
        right.append((px - nx * half, py - ny * half))
    return (left, right)


def split_ring_into_rails(ring: Ring) -> tuple[list[Point], list[Point]]:
    """Split a closed narrow contour into two rails along its long axis.

    Picks the vertex farthest from the centroid, then the vertex farthest from
    that one; those two points are the ends of the column.  Robust for the
    elongated shapes satin is actually used on (strokes, letters, borders).
    """
    pts = dedupe(ring, 0.5)
    if len(pts) > 2 and distance(pts[0], pts[-1]) < 0.5:
        pts = pts[:-1]
    n = len(pts)
    if n < 4:
        return ([], [])

    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    start = max(range(n), key=lambda i: distance(pts[i], (cx, cy)))
    end = max(range(n), key=lambda i: distance(pts[i], pts[start]))
    if start == end:
        return ([], [])

    lo, hi = (start, end) if start < end else (end, start)
    rail_a = pts[lo : hi + 1]
    rail_b = pts[hi:] + pts[: lo + 1]
    rail_b.reverse()
    if len(rail_a) < 2 or len(rail_b) < 2:
        return ([], [])
    return (rail_a, rail_b)


def column_width(rail_a: list[Point], rail_b: list[Point], samples: int = 12) -> float:
    """Average perpendicular width of a column, used to pick satin vs fill."""
    if not rail_a or not rail_b:
        return 0.0
    a = resample_path(rail_a, max(1.0, _path_length(rail_a) / samples), keep_corners=False)
    b = resample_path(rail_b, max(1.0, _path_length(rail_b) / samples), keep_corners=False)
    count = min(len(a), len(b))
    if count == 0:
        return 0.0
    total = sum(distance(a[i], b[i]) for i in range(count))
    return total / count


def _path_length(path: list[Point]) -> float:
    return sum(distance(path[i], path[i + 1]) for i in range(len(path) - 1))


def _align_rails(
    rail_a: list[Point], rail_b: list[Point], spacing: float
) -> tuple[list[Point], list[Point]]:
    """Resample both rails to the same number of points, stepped by ``spacing``
    along the longer rail so density stays even through curves."""
    len_a, len_b = _path_length(rail_a), _path_length(rail_b)
    longest = max(len_a, len_b)
    if longest <= 0:
        return ([], [])
    steps = max(2, int(longest / max(spacing, 0.5)))
    a = _resample_to_count(rail_a, steps)
    b = _resample_to_count(rail_b, steps)
    return (a, b)


def _resample_to_count(path: list[Point], count: int) -> list[Point]:
    total = _path_length(path)
    if total <= 0 or count < 2:
        return list(path)
    step = total / (count - 1)
    out: list[Point] = [path[0]]
    target = step
    travelled = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        seg = distance(a, b)
        if seg == 0:
            continue
        while travelled + seg >= target and len(out) < count:
            ratio = (target - travelled) / seg
            out.append((a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio))
            target += step
        travelled += seg
    while len(out) < count:
        out.append(path[-1])
    return out


def _split_leg(a: Point, b: Point, max_length: float) -> list[Point]:
    """Break a wide satin leg into anchored sub-stitches so it cannot snag."""
    length = distance(a, b)
    if length <= max_length or max_length <= 0:
        return [b]
    parts = int(math.ceil(length / max_length))
    return [
        (a[0] + (b[0] - a[0]) * (i / parts), a[1] + (b[1] - a[1]) * (i / parts))
        for i in range(1, parts + 1)
    ]


def generate_satin(
    rail_a: list[Point],
    rail_b: list[Point],
    params: SatinParams,
) -> list[Point]:
    """Zigzag between two rails, returning one continuous stitch run."""
    a, b = _align_rails(rail_a, rail_b, params.spacing)
    if len(a) < 2 or len(b) < 2:
        return []

    comp = params.pull_compensation
    stitches: list[Point] = []
    for i in range(len(a)):
        pa, pb = a[i], b[i]
        if comp > 0:
            width = distance(pa, pb)
            if width > 1e-6:
                ux, uy = (pb[0] - pa[0]) / width, (pb[1] - pa[1]) / width
                pa = (pa[0] - ux * comp, pa[1] - uy * comp)
                pb = (pb[0] + ux * comp, pb[1] + uy * comp)

        first, second = (pa, pb) if i % 2 == 0 else (pb, pa)
        if not stitches:
            stitches.append(first)
        else:
            stitches.extend(_split_leg(stitches[-1], first, params.split_stitch))
        stitches.extend(_split_leg(first, second, params.split_stitch))

    return stitches


def satin_from_centerline(
    centerline: list[Point], width: float, params: SatinParams
) -> list[Point]:
    rail_a, rail_b = rails_from_centerline(centerline, width)
    if not rail_a:
        return []
    return generate_satin(rail_a, rail_b, params)


def satin_from_ring(ring: Ring, params: SatinParams) -> list[Point]:
    rail_a, rail_b = split_ring_into_rails(ring)
    if not rail_a:
        return []
    return generate_satin(rail_a, rail_b, params)
