"""Stitch-order optimisation and machine-safety passes.

Everything here runs *after* the stitch generators and *before* the format
writers.  It is the difference between a file that sews and a file that
birdnests: tie-ins, tie-offs, trim decisions, colour consolidation, jump
minimisation and stitch-length clamping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Point, distance
from .pattern import Command, EmbroideryPattern, StitchBlock, Thread


@dataclass(slots=True)
class StitchRun:
    """A continuous run of needle penetrations in one colour."""

    points: list[Point]
    thread: Thread
    technique: str = "fill"
    label: str = ""
    order_hint: int = 0


@dataclass(slots=True)
class AssemblyParams:
    max_stitch: float = 120.0
    min_stitch: float = 6.0
    trim_threshold: float = 250.0    # travel over 25 mm gets a trim
    jump_threshold: float = 60.0     # travel over 6 mm becomes a jump
    tie_in: bool = True
    tie_off: bool = True
    tie_stitch_length: float = 8.0   # 0.8 mm
    tie_stitch_count: int = 3
    merge_colors: bool = True


# --------------------------------------------------------------------- ties
def tie_in_stitches(run: list[Point], length: float, count: int) -> list[Point]:
    """Short anchoring stitches into the start of a run so it cannot pull out."""
    if len(run) < 2:
        return []
    start, nxt = run[0], run[1]
    total = distance(start, nxt)
    if total == 0:
        return []
    ux, uy = (nxt[0] - start[0]) / total, (nxt[1] - start[1]) / total
    step = min(length, total)
    out: list[Point] = []
    for i in range(count):
        target = start if i % 2 else (start[0] + ux * step, start[1] + uy * step)
        out.append(target)
    return out


def tie_off_stitches(run: list[Point], length: float, count: int) -> list[Point]:
    """Mirror of :func:`tie_in_stitches` at the end of a run."""
    if len(run) < 2:
        return []
    end, prev = run[-1], run[-2]
    total = distance(end, prev)
    if total == 0:
        return []
    ux, uy = (prev[0] - end[0]) / total, (prev[1] - end[1]) / total
    step = min(length, total)
    out: list[Point] = []
    for i in range(count):
        target = end if i % 2 else (end[0] + ux * step, end[1] + uy * step)
        out.append(target)
    return out


# ------------------------------------------------------------------ clamping
def clamp_stitch_lengths(points: list[Point], max_stitch: float, min_stitch: float) -> list[Point]:
    """Split over-long stitches and drop degenerate ones.

    Every machine has a maximum movement per stitch (typically 12.7 mm); a
    file that exceeds it either errors out or thread-breaks.
    """
    if not points:
        return []
    out: list[Point] = [points[0]]
    for target in points[1:]:
        prev = out[-1]
        d = distance(prev, target)
        if d < min_stitch:
            continue
        if d > max_stitch:
            parts = int(math.ceil(d / max_stitch))
            for i in range(1, parts + 1):
                out.append(
                    (
                        prev[0] + (target[0] - prev[0]) * i / parts,
                        prev[1] + (target[1] - prev[1]) * i / parts,
                    )
                )
        else:
            out.append(target)
    return out


# ------------------------------------------------------------ colour ordering
def order_runs(runs: list[StitchRun]) -> list[StitchRun]:
    """Group runs by thread colour to minimise colour changes, keeping the
    caller's intent: underlay before top stitching, and a stable order within
    each colour so results are reproducible."""
    buckets: dict[tuple[int, int, int], list[StitchRun]] = {}
    order: list[tuple[int, int, int]] = []
    for run in runs:
        key = run.thread.rgb
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(run)

    out: list[StitchRun] = []
    for key in order:
        group = buckets[key]
        group.sort(key=lambda r: (r.order_hint, 0 if r.technique == "underlay" else 1))
        out.extend(_nearest_neighbour_chain(group))
    return out


def _nearest_neighbour_chain(runs: list[StitchRun]) -> list[StitchRun]:
    """Greedy travel-minimising order within one colour, preserving the
    underlay-before-top invariant by chaining inside each order_hint band."""
    if len(runs) < 3:
        return runs

    bands: dict[int, list[StitchRun]] = {}
    for run in runs:
        bands.setdefault(run.order_hint, []).append(run)

    out: list[StitchRun] = []
    cursor: Point = (0.0, 0.0)
    for hint in sorted(bands):
        pool = list(bands[hint])
        while pool:
            best_index = min(
                range(len(pool)),
                key=lambda i: distance(cursor, pool[i].points[0]) if pool[i].points else 1e18,
            )
            chosen = pool.pop(best_index)
            if chosen.points:
                cursor = chosen.points[-1]
            out.append(chosen)
    return out


# ------------------------------------------------------------------ assembly
def assemble(
    runs: list[StitchRun],
    params: AssemblyParams,
    name: str = "design",
) -> EmbroideryPattern:
    """Turn independent stitch runs into a machine-ready pattern.

    Inserts jumps, trims, tie-ins and tie-offs, and splits colour blocks.
    """
    pattern = EmbroideryPattern(name=name)
    ordered = order_runs(runs) if params.merge_colors else runs

    current_block: StitchBlock | None = None
    cursor: Point | None = None
    cursor_prev: Point | None = None

    for run in ordered:
        points = clamp_stitch_lengths(run.points, params.max_stitch, params.min_stitch)
        if len(points) < 2:
            continue

        if current_block is None or current_block.thread.rgb != run.thread.rgb:
            current_block = pattern.new_block(run.thread, label=run.label, technique=run.technique)
            cursor = None
            cursor_prev = None
        elif run.label and not current_block.label:
            current_block.label = run.label

        if cursor is not None:
            gap = distance(cursor, points[0])
            if gap > params.trim_threshold:
                # Tie off by retracing the run we just finished. Anchoring on
                # anything else drags the needle across the design before the
                # trim, which is a guaranteed thread break.
                if params.tie_off and cursor_prev is not None:
                    for p in tie_off_stitches(
                        [cursor_prev, cursor], params.tie_stitch_length, params.tie_stitch_count
                    ):
                        current_block.add(p[0], p[1])
                current_block.add(cursor[0], cursor[1], Command.TRIM)
                current_block.add(points[0][0], points[0][1], Command.JUMP)
            elif gap > params.jump_threshold:
                for p in clamp_stitch_lengths([cursor, points[0]], params.max_stitch, params.min_stitch)[1:]:
                    current_block.add(p[0], p[1], Command.JUMP)
            elif gap > params.min_stitch:
                current_block.add(points[0][0], points[0][1])

        if params.tie_in and (cursor is None or distance(cursor, points[0]) > params.jump_threshold):
            for p in tie_in_stitches(points, params.tie_stitch_length, params.tie_stitch_count):
                current_block.add(p[0], p[1])

        for p in points:
            current_block.add(p[0], p[1])
        cursor = points[-1]
        cursor_prev = points[-2]

    # Final tie-off so the last block cannot unravel on the trimmer.
    if params.tie_off and pattern.blocks:
        last = pattern.blocks[-1]
        pts = [(s.x, s.y) for s in last.stitches[-2:]]
        if len(pts) == 2:
            for p in tie_off_stitches(pts, params.tie_stitch_length, params.tie_stitch_count):
                last.add(p[0], p[1])

    pattern.blocks = [b for b in pattern.blocks if len(b.stitches) > 1]
    _label_blocks(pattern, ordered)
    return pattern


def _label_blocks(pattern: EmbroideryPattern, runs: list[StitchRun]) -> None:
    """A block usually mixes underlay and top stitching; report the technique
    and label that dominate it rather than whichever run happened to be first."""
    weights: dict[tuple[int, int, int], dict[str, int]] = {}
    labels: dict[tuple[int, int, int], str] = {}
    for run in runs:
        key = run.thread.rgb
        bucket = weights.setdefault(key, {})
        bucket[run.technique] = bucket.get(run.technique, 0) + len(run.points)
        if run.technique != "underlay" and key not in labels and run.label:
            labels[key] = run.label

    for block in pattern.blocks:
        bucket = weights.get(block.thread.rgb)
        if not bucket:
            continue
        visible = {k: v for k, v in bucket.items() if k != "underlay"} or bucket
        block.technique = max(visible, key=visible.get)
        block.label = labels.get(block.thread.rgb, block.label)


def validate_pattern(
    pattern: EmbroideryPattern,
    hoop_width_mm: float | None = None,
    hoop_height_mm: float | None = None,
    max_stitches: int | None = None,
    max_colors: int | None = None,
) -> list[dict]:
    """Pre-flight checks against a machine profile.  Returns a list of issues."""
    issues: list[dict] = []
    width_mm, height_mm = pattern.size_mm()

    if hoop_width_mm and width_mm > hoop_width_mm:
        issues.append({
            "level": "error",
            "code": "hoop_width",
            "message": f"Design is {width_mm:.1f} mm wide; hoop allows {hoop_width_mm:.1f} mm.",
        })
    if hoop_height_mm and height_mm > hoop_height_mm:
        issues.append({
            "level": "error",
            "code": "hoop_height",
            "message": f"Design is {height_mm:.1f} mm tall; hoop allows {hoop_height_mm:.1f} mm.",
        })
    if max_stitches and pattern.stitch_count > max_stitches:
        issues.append({
            "level": "error",
            "code": "stitch_limit",
            "message": f"{pattern.stitch_count:,} stitches exceeds the machine limit "
                       f"of {max_stitches:,}.",
        })
    if max_colors and pattern.color_count > max_colors:
        issues.append({
            "level": "warning",
            "code": "color_limit",
            "message": f"{pattern.color_count} colours but only {max_colors} needles; "
                       "the operator will need a manual thread change.",
        })
    if pattern.stitch_count == 0:
        issues.append({
            "level": "error",
            "code": "empty",
            "message": "No stitches were generated. Check that the artwork has filled shapes.",
        })
    # A standard tatami fill (0.4 mm rows, 4 mm stitches) lands near
    # 60 stitches/cm². Well above that and the fabric boards up.
    density = _density_per_cm2(pattern)
    if density > 180:
        issues.append({
            "level": "warning",
            "code": "high_density",
            "message": f"~{density:.0f} stitches/cm² is very dense; expect thread breaks "
                       "and a stiff hand. Consider opening the fill spacing.",
        })
    return issues


def _density_per_cm2(pattern: EmbroideryPattern) -> float:
    width_mm, height_mm = pattern.size_mm()
    area_cm2 = (width_mm / 10.0) * (height_mm / 10.0)
    if area_cm2 <= 0:
        return 0.0
    return pattern.stitch_count / area_cm2
