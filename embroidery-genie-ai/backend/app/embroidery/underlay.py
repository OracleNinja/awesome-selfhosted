"""Underlay generation.

Underlay is the foundation layer sewn before the visible top stitching.  It
stabilises the fabric, lifts the pile so the design does not sink, and gives
the top stitches something to bite into.  Skipping it is the single most
common reason a design puckers.

Types implemented:

``center_run``  a single run down the middle of the shape
``edge_run``    a run just inside the outline (holds the edge)
``zigzag``      an open zigzag across the shape (lifts pile, adds body)
``tatami``      a very open fill (used under large solid areas)
"""

from __future__ import annotations

import math
from typing import Sequence

from shapely.geometry import Polygon

from .fills import FillParams, generate_fill
from .geometry import Point, offset_polygon, resample_path, to_polygons
from .satin import SatinParams, generate_satin, split_ring_into_rails


def _inset(polygon: Polygon, amount: float) -> list[Polygon]:
    if amount <= 0:
        return [polygon]
    return offset_polygon(polygon, -amount)


def edge_run(polygon: Polygon, inset: float, stitch_length: float = 25.0) -> list[list[Point]]:
    """Run around the outline, set in from the edge so the top stitching
    covers it."""
    runs: list[list[Point]] = []
    for piece in _inset(polygon, inset):
        ring = [(float(x), float(y)) for x, y in piece.exterior.coords]
        path = resample_path(ring, stitch_length, keep_corners=True)
        if len(path) > 2:
            runs.append(path)
        for interior in piece.interiors:
            hole = [(float(x), float(y)) for x, y in interior.coords]
            hole_path = resample_path(hole, stitch_length, keep_corners=True)
            if len(hole_path) > 2:
                runs.append(hole_path)
    return runs


def center_run(polygon: Polygon, stitch_length: float = 25.0) -> list[list[Point]]:
    """Approximate centre line via the long axis of a heavily inset copy.

    For a narrow shape this is the true spine; for a blob it is a stabilising
    pass through the middle, which is exactly what centre-run underlay wants.
    """
    shrunk = _inset(polygon, min(polygon.length / 40.0, 15.0))
    target = shrunk[0] if shrunk else polygon
    rail_a, rail_b = split_ring_into_rails(
        [(float(x), float(y)) for x, y in target.exterior.coords]
    )
    if not rail_a or not rail_b:
        return []
    count = min(len(rail_a), len(rail_b))
    spine = [
        ((rail_a[i][0] + rail_b[len(rail_b) - 1 - i][0]) / 2.0,
         (rail_a[i][1] + rail_b[len(rail_b) - 1 - i][1]) / 2.0)
        for i in range(count)
    ]
    path = resample_path(spine, stitch_length, keep_corners=False)
    return [path] if len(path) > 2 else []


def zigzag_underlay(
    polygon: Polygon, inset: float, spacing: float, angle_deg: float = 0.0
) -> list[list[Point]]:
    """Open zigzag across the shape: cheap stitch count, big stability gain."""
    runs: list[list[Point]] = []
    for piece in _inset(polygon, inset):
        ring = [(float(x), float(y)) for x, y in piece.exterior.coords]
        rail_a, rail_b = split_ring_into_rails(ring)
        if not rail_a or not rail_b:
            continue
        stitches = generate_satin(
            rail_a,
            rail_b,
            SatinParams(spacing=spacing, pull_compensation=0.0, split_stitch=120.0),
        )
        if len(stitches) > 2:
            runs.append(stitches)
    if runs:
        return runs
    # Fall back to a very open fill when the shape will not split into rails.
    return tatami_underlay(polygon, inset, spacing, angle_deg)


def tatami_underlay(
    polygon: Polygon, inset: float, spacing: float, angle_deg: float = 0.0
) -> list[list[Point]]:
    runs: list[list[Point]] = []
    for piece in _inset(polygon, inset):
        runs.extend(
            generate_fill(
                piece,
                FillParams(
                    spacing=spacing,
                    angle_deg=angle_deg,
                    max_stitch=120.0,
                    stagger=2,
                    travel_threshold=80.0,
                ),
            )
        )
    return runs


def build_underlay(
    polygon: Polygon,
    types: Sequence[str],
    inset: float,
    spacing: float,
    top_angle_deg: float = 45.0,
) -> list[tuple[str, list[Point]]]:
    """Run the configured underlay recipe, returning ``(type, run)`` pairs.

    Underlay always runs roughly perpendicular to the top stitching so the two
    layers lock together rather than sinking into the same needle holes.
    """
    under_angle = (top_angle_deg + 90.0) % 180.0
    out: list[tuple[str, list[Point]]] = []
    for kind in types:
        if kind == "center_run":
            runs = center_run(polygon)
        elif kind == "edge_run":
            runs = edge_run(polygon, inset)
        elif kind == "zigzag":
            runs = zigzag_underlay(polygon, inset, spacing, under_angle)
        elif kind == "tatami":
            runs = tatami_underlay(polygon, inset, spacing, under_angle)
        else:
            continue
        out.extend((kind, run) for run in runs if len(run) > 1)
    return out


def recommended_density_scale(area_units: float, fabric_speed: float) -> float:
    """Very large solid areas want slightly opener density or they board up.

    Returns a multiplier applied to the fabric's base row spacing.
    """
    area_cm2 = area_units / 100.0 / 100.0  # units^2 -> cm^2
    if area_cm2 <= 0:
        return 1.0
    scale = 1.0 + min(0.25, math.log10(max(area_cm2, 1.0)) * 0.12)
    return scale * (1.0 + (1.0 - fabric_speed) * 0.05)
