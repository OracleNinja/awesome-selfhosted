"""Geometry helpers for stitch generation.

Shapely does the heavy lifting for boolean ops and offsetting (pull
compensation, underlay inset).  Everything here works in internal units
(0.1 mm) and returns plain tuples so the stitch generators stay simple.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.ops import unary_union

Point = tuple[float, float]
Ring = list[Point]


@dataclass(slots=True)
class Region:
    """A closed area to be stitched: one outer ring plus optional holes."""

    outer: Ring
    holes: list[Ring]

    def to_polygon(self) -> Polygon:
        return Polygon(self.outer, self.holes)


# --------------------------------------------------------------------- basics
def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polygon_area(ring: Sequence[Point]) -> float:
    """Signed area (positive = counter-clockwise in a Y-up frame)."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def perimeter(ring: Sequence[Point], closed: bool = True) -> float:
    total = 0.0
    for i in range(len(ring) - 1):
        total += distance(ring[i], ring[i + 1])
    if closed and len(ring) > 2:
        total += distance(ring[-1], ring[0])
    return total


def rotate_point(p: Point, angle_rad: float, origin: Point = (0.0, 0.0)) -> Point:
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    dx, dy = p[0] - origin[0], p[1] - origin[1]
    return (origin[0] + dx * cos_a - dy * sin_a, origin[1] + dx * sin_a + dy * cos_a)


def rotate_ring(ring: Sequence[Point], angle_rad: float, origin: Point = (0.0, 0.0)) -> Ring:
    return [rotate_point(p, angle_rad, origin) for p in ring]


def bounds_of(points: Iterable[Point]) -> tuple[float, float, float, float]:
    pts = list(points)
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# ------------------------------------------------------------------- cleaning
def dedupe(points: Sequence[Point], tolerance: float = 0.5) -> Ring:
    """Drop consecutive points closer than ``tolerance`` units apart."""
    out: Ring = []
    for p in points:
        if not out or distance(out[-1], p) >= tolerance:
            out.append((float(p[0]), float(p[1])))
    return out


def simplify_ring(ring: Sequence[Point], tolerance: float = 2.0) -> Ring:
    """Ramer-Douglas-Peucker; keeps embroidery outlines smooth without the
    thousands of near-collinear points a raster tracer emits."""
    pts = dedupe(ring, 0.25)
    if len(pts) < 3:
        return pts

    def rdp(sub: Sequence[Point]) -> Ring:
        if len(sub) < 3:
            return list(sub)
        start, end = sub[0], sub[-1]
        dx, dy = end[0] - start[0], end[1] - start[1]
        seg_len = math.hypot(dx, dy)
        max_dist, index = 0.0, 0
        for i in range(1, len(sub) - 1):
            px, py = sub[i]
            if seg_len == 0:
                d = distance(sub[i], start)
            else:
                d = abs(dy * px - dx * py + end[0] * start[1] - end[1] * start[0]) / seg_len
            if d > max_dist:
                max_dist, index = d, i
        if max_dist <= tolerance:
            return [start, end]
        left = rdp(sub[: index + 1])
        right = rdp(sub[index:])
        return left[:-1] + right

    return rdp(pts)


def smooth_ring(ring: Sequence[Point], strength: float = 0.35, passes: int = 1) -> Ring:
    """Chaikin-style corner relaxation used to take the staircase out of
    contours traced from a bitmap."""
    pts = list(ring)
    if len(pts) < 4:
        return pts
    for _ in range(max(0, passes)):
        out: Ring = []
        n = len(pts)
        for i in range(n):
            prev_p = pts[(i - 1) % n]
            cur = pts[i]
            next_p = pts[(i + 1) % n]
            out.append(
                (
                    cur[0] + strength * ((prev_p[0] + next_p[0]) / 2.0 - cur[0]),
                    cur[1] + strength * ((prev_p[1] + next_p[1]) / 2.0 - cur[1]),
                )
            )
        pts = out
    return pts


# ------------------------------------------------------------------ resampling
def resample_path(points: Sequence[Point], step: float, keep_corners: bool = True) -> Ring:
    """Walk a polyline placing a point every ``step`` units.

    ``keep_corners`` preserves sharp vertices (>35 deg turn) so satin columns
    and running stitches do not round off detail.
    """
    pts = dedupe(points, 0.1)
    if len(pts) < 2 or step <= 0:
        return pts

    corners: set[int] = set()
    if keep_corners:
        for i in range(1, len(pts) - 1):
            a, b, c = pts[i - 1], pts[i], pts[i + 1]
            v1 = (b[0] - a[0], b[1] - a[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            n1 = math.hypot(*v1)
            n2 = math.hypot(*v2)
            if n1 == 0 or n2 == 0:
                continue
            cos_t = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
            if math.degrees(math.acos(cos_t)) > 35.0:
                corners.add(i)

    out: Ring = [pts[0]]
    carry = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = distance(a, b)
        if seg == 0:
            continue
        ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
        travelled = step - carry
        while travelled <= seg:
            out.append((a[0] + ux * travelled, a[1] + uy * travelled))
            travelled += step
        carry = seg - (travelled - step)
        if i + 1 in corners and distance(out[-1], b) > 0.5:
            out.append(b)
            carry = 0.0
    if distance(out[-1], pts[-1]) > 0.5:
        out.append(pts[-1])
    return out


# ---------------------------------------------------------------- shapely glue
def to_polygons(geom) -> list[Polygon]:
    """Normalise any shapely result into a list of valid polygons."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > 0 else []
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        out: list[Polygon] = []
        for part in geom.geoms:
            out.extend(to_polygons(part))
        return out
    return []


def make_valid(polygon: Polygon) -> Polygon | MultiPolygon | None:
    """Self-intersecting contours are common from tracers; a zero buffer is the
    standard repair and keeps the area intact."""
    if polygon.is_valid:
        return polygon
    fixed = polygon.buffer(0)
    return fixed if not fixed.is_empty else None


def region_to_polygons(region: Region) -> list[Polygon]:
    poly = region.to_polygon()
    fixed = make_valid(poly)
    return to_polygons(fixed)


def offset_polygon(polygon: Polygon, amount: float) -> list[Polygon]:
    """Grow (positive) or shrink (negative) a polygon.

    Used for pull compensation and for insetting underlay away from the edge.
    """
    if abs(amount) < 1e-6:
        return [polygon]
    grown = polygon.buffer(amount, join_style=2, mitre_limit=2.5)
    return to_polygons(grown)


def union_polygons(polygons: Sequence[Polygon]) -> list[Polygon]:
    if not polygons:
        return []
    return to_polygons(unary_union(list(polygons)))


def clip_line(polygon: Polygon, line: LineString) -> list[list[Point]]:
    """Intersect a line with a polygon, returning the inside segments."""
    try:
        inter = polygon.intersection(line)
    except Exception:  # pragma: no cover - shapely topology edge case
        inter = polygon.buffer(0).intersection(line)
    if inter.is_empty:
        return []
    if isinstance(inter, LineString):
        return [list(inter.coords)]
    if isinstance(inter, (MultiLineString, GeometryCollection)):
        out: list[list[Point]] = []
        for part in inter.geoms:
            if isinstance(part, LineString) and part.length > 0:
                out.append(list(part.coords))
        return out
    return []


def polygon_to_region(polygon: Polygon) -> Region:
    return Region(
        outer=[(float(x), float(y)) for x, y in polygon.exterior.coords],
        holes=[[(float(x), float(y)) for x, y in ring.coords] for ring in polygon.interiors],
    )


def medial_axis_estimate(polygon: Polygon) -> float:
    """Cheap estimate of a shape's average width, used to decide between satin
    and fill.  ``4 * area / perimeter`` is exact for a long thin rectangle."""
    if polygon.length == 0:
        return 0.0
    return 4.0 * polygon.area / polygon.length
