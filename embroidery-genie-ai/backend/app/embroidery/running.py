"""Running stitch, bean stitch and travel-path generation."""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import Point, dedupe, distance, resample_path


@dataclass(slots=True)
class RunParams:
    stitch_length: float = 25.0   # 2.5 mm
    repeats: int = 1              # 3 = bean stitch (there, back, there)
    keep_corners: bool = True


def generate_running(path: list[Point], params: RunParams) -> list[Point]:
    """Even running stitch along a path."""
    pts = resample_path(dedupe(path, 0.3), params.stitch_length, params.keep_corners)
    if len(pts) < 2:
        return []
    if params.repeats <= 1:
        return pts
    out: list[Point] = list(pts)
    for i in range(1, params.repeats):
        leg = list(reversed(pts)) if i % 2 == 1 else list(pts)
        out.extend(leg[1:])
    return out


def generate_bean(path: list[Point], stitch_length: float = 25.0) -> list[Point]:
    """Triple-pass bean stitch: heavier outline without changing colour."""
    return generate_running(path, RunParams(stitch_length=stitch_length, repeats=3))


def close_path(path: list[Point]) -> list[Point]:
    if len(path) > 2 and distance(path[0], path[-1]) > 0.5:
        return list(path) + [path[0]]
    return list(path)


def travel_stitches(start: Point, end: Point, stitch_length: float = 30.0) -> list[Point]:
    """Straight travel run between two points, split into legal stitches."""
    total = distance(start, end)
    if total <= stitch_length:
        return [end]
    return resample_path([start, end], stitch_length, keep_corners=False)[1:]
