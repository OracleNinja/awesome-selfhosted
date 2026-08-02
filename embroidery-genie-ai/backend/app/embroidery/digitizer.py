"""The digitizing engine.

Takes vector artwork plus a fabric profile and produces a machine-ready
:class:`EmbroideryPattern`.  This is where the craft decisions live:

* which shapes become satin columns and which become tatami fill
* what underlay each shape needs on this fabric
* how much pull compensation to add
* what angle each fill runs at so adjacent shapes do not read as one blob
* what order everything sews in

The rules encoded here are conventional commercial digitizing practice, not
guesses: satin under ~10 mm wide, fill above; underlay perpendicular to top
stitching; compensation scaled to fabric stability; small shapes get a
run instead of a fill they cannot hold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Polygon

from .fabrics import FabricProfile, get_fabric
from .fills import FillParams, generate_fill
from .geometry import (
    Point,
    make_valid,
    medial_axis_estimate,
    offset_polygon,
    simplify_ring,
    smooth_ring,
    to_polygons,
    union_polygons,
)
from .optimizer import AssemblyParams, StitchRun, assemble, validate_pattern
from .pattern import EmbroideryPattern, Thread
from .running import RunParams, close_path, generate_running
from .satin import SatinParams, generate_satin, satin_from_ring, split_ring_into_rails
from .svg_parse import SvgDocument, SvgShape, parse_svg
from .threads import get_catalog
from .underlay import build_underlay, recommended_density_scale
from .units import mm_to_units, units_to_mm

# A shape narrower than this is stitched as a run, not a fill or satin.
MIN_STITCHABLE_WIDTH_MM = 0.8
# Fills below this area are not worth the trims; they become satin or a run.
MIN_FILL_AREA_MM2 = 12.0


@dataclass
class DigitizeOptions:
    fabric: str = "cotton_shirt"
    thread_catalog: str = "genie_standard"
    target_width_mm: float | None = None
    target_height_mm: float | None = None
    fill_angle_deg: float = 45.0
    density_scale: float = 1.0          # >1 opens the fill, <1 tightens it
    auto_underlay: bool = True
    auto_satin: bool = True
    stroke_as_satin: bool = True
    center_design: bool = True
    tie_stitches: bool = True
    vary_fill_angle: bool = True        # rotate angle per colour for definition
    max_colors: int | None = None
    # Machine limits, used for pre-flight validation only.
    hoop_width_mm: float | None = None
    hoop_height_mm: float | None = None
    max_stitches: int | None = None
    needle_count: int | None = None


@dataclass
class DigitizeResult:
    pattern: EmbroideryPattern
    issues: list[dict] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)
    fabric: str = "cotton_shirt"
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            **self.pattern.summary(),
            "issues": self.issues,
            "objects": self.objects,
            "fabric": self.fabric,
            "options": self.options,
        }


# ------------------------------------------------------------ shape assembly
def _shape_polygons(shape: SvgShape) -> list[Polygon]:
    """Build shapely polygons from a shape's rings, treating contained rings
    as holes (the even-odd behaviour real logo art relies on)."""
    rings = [simplify_ring(r, 1.5) for r in shape.rings]
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return []

    candidates: list[Polygon] = []
    for ring in rings:
        poly = make_valid(Polygon(ring))
        candidates.extend(to_polygons(poly))
    if not candidates:
        return []

    candidates.sort(key=lambda p: p.area, reverse=True)
    outers: list[Polygon] = []
    for poly in candidates:
        placed = False
        for i, outer in enumerate(outers):
            if outer.contains(poly.representative_point()):
                outers[i] = outer.difference(poly)
                placed = True
                break
        if not placed:
            outers.append(poly)

    result: list[Polygon] = []
    for poly in outers:
        result.extend(to_polygons(poly))
    return [p for p in result if p.area > 0]


def _stroke_polygon(shape: SvgShape) -> list[Polygon]:
    """Buffer a stroked path into an area so it can be satin-stitched."""
    if shape.stroke is None or shape.stroke_width <= 0:
        return []
    half = shape.stroke_width / 2.0
    polys: list[Polygon] = []
    for ring in shape.rings:
        if len(ring) < 2:
            continue
        path = close_path(ring) if shape.closed else list(ring)
        try:
            buffered = LineString(path).buffer(half, cap_style=2, join_style=2)
        except Exception:
            continue
        polys.extend(to_polygons(buffered))
    return polys


# --------------------------------------------------------------- stitch plan
def _fill_angle_for(index: int, base: float, vary: bool) -> float:
    if not vary:
        return base
    # 30 deg steps keep adjacent colours visually separated without ever
    # landing on 0/90, which show needle lines on most fabrics.
    return (base + index * 30.0) % 180.0


def _plan_area(
    polygon: Polygon,
    thread: Thread,
    fabric: FabricProfile,
    options: DigitizeOptions,
    angle_deg: float,
    label: str,
    order_hint: int,
) -> tuple[list[StitchRun], dict]:
    """Decide satin vs fill vs run for one closed area and emit its runs."""
    runs: list[StitchRun] = []
    width_units = medial_axis_estimate(polygon)
    width_mm = units_to_mm(width_units)
    area_mm2 = polygon.area / 100.0  # units^2 -> mm^2

    info = {
        "label": label,
        "color": thread.hex,
        "thread": thread.name,
        "width_mm": round(width_mm, 2),
        "area_mm2": round(area_mm2, 2),
    }

    if width_mm < MIN_STITCHABLE_WIDTH_MM:
        # Too narrow to hold any thread coverage: sew the spine as a run.
        ring = [(float(x), float(y)) for x, y in polygon.exterior.coords]
        rail_a, rail_b = split_ring_into_rails(ring)
        if rail_a and rail_b:
            count = min(len(rail_a), len(rail_b))
            spine = [
                ((rail_a[i][0] + rail_b[len(rail_b) - 1 - i][0]) / 2.0,
                 (rail_a[i][1] + rail_b[len(rail_b) - 1 - i][1]) / 2.0)
                for i in range(count)
            ]
            points = generate_running(spine, RunParams(stitch_length=mm_to_units(2.0), repeats=3))
            if len(points) > 1:
                runs.append(StitchRun(points, thread, "running", label, order_hint + 1))
        info["technique"] = "running"
        return runs, info

    use_satin = (
        options.auto_satin
        and width_mm <= fabric.max_satin_width_mm
        and area_mm2 >= 2.0
    )

    # Underlay first, always.
    if options.auto_underlay and fabric.underlay and area_mm2 >= 4.0:
        recipe = fabric.underlay
        if use_satin:
            # Satin columns want a light foundation, not a full tatami.
            recipe = tuple(k for k in recipe if k in ("center_run", "edge_run", "zigzag"))
        for kind, run in build_underlay(
            polygon,
            recipe,
            inset=fabric.underlay_inset,
            spacing=fabric.underlay_spacing,
            top_angle_deg=angle_deg,
        ):
            runs.append(StitchRun(run, thread, "underlay", f"{label} underlay ({kind})", order_hint))

    if use_satin:
        ring = [(float(x), float(y)) for x, y in polygon.exterior.coords]
        points = satin_from_ring(
            smooth_ring(ring, 0.25, 1),
            SatinParams(
                spacing=fabric.satin_spacing * options.density_scale,
                pull_compensation=fabric.pull_compensation,
                max_width=fabric.max_satin_width,
                split_stitch=fabric.max_stitch,
            ),
        )
        if len(points) > 2:
            runs.append(StitchRun(points, thread, "satin", label, order_hint + 1))
            info["technique"] = "satin"
            return runs, info
        # Rail split failed (blobby shape) - fall through to fill.

    if area_mm2 < MIN_FILL_AREA_MM2:
        ring = [(float(x), float(y)) for x, y in polygon.exterior.coords]
        points = generate_running(close_path(ring), RunParams(stitch_length=mm_to_units(1.8), repeats=2))
        if len(points) > 1:
            runs.append(StitchRun(points, thread, "running", label, order_hint + 1))
        info["technique"] = "running"
        return runs, info

    # Pull compensation: grow the shape so it finishes at the drawn size.
    compensated = offset_polygon(polygon, fabric.pull_compensation)
    targets = compensated or [polygon]

    density_scale = options.density_scale * recommended_density_scale(
        polygon.area, fabric.speed_factor
    )
    for target in targets:
        fill_runs = generate_fill(
            target,
            FillParams(
                spacing=fabric.fill_spacing * density_scale,
                angle_deg=angle_deg,
                max_stitch=fabric.fill_stitch,
                min_stitch=fabric.min_stitch,
                stagger=4,
                travel_threshold=mm_to_units(6.0),
            ),
        )
        for run in fill_runs:
            if len(run) > 1:
                runs.append(StitchRun(run, thread, "fill", label, order_hint + 1))

    info["technique"] = "fill"
    info["angle_deg"] = round(angle_deg, 1)
    return runs, info


def _quantize_colors(
    groups: list[tuple[tuple[int, int, int], list[Polygon], str]], max_colors: int
) -> list[tuple[tuple[int, int, int], list[Polygon], str]]:
    """Merge the least-used colours into their nearest neighbour until the
    design fits the requested colour budget."""
    from .threads import color_distance

    working = list(groups)
    while len(working) > max_colors:
        working.sort(key=lambda g: sum(p.area for p in g[1]))
        smallest = working.pop(0)
        if not working:
            working.append(smallest)
            break
        nearest = min(working, key=lambda g: color_distance(g[0], smallest[0]))
        nearest[1].extend(smallest[1])
    return working


def digitize_shapes(
    shapes: list[SvgShape],
    options: DigitizeOptions | None = None,
) -> DigitizeResult:
    """Core entry point: vector shapes -> machine pattern."""
    options = options or DigitizeOptions()
    fabric = get_fabric(options.fabric)
    catalog = get_catalog(options.thread_catalog)

    # 1. Group filled areas by colour, preserving paint order.
    color_groups: list[tuple[tuple[int, int, int], list[Polygon], str]] = []
    index_by_color: dict[tuple[int, int, int], int] = {}

    def bucket(color: tuple[int, int, int], polys: list[Polygon], label: str) -> None:
        if not polys:
            return
        if color in index_by_color:
            color_groups[index_by_color[color]][1].extend(polys)
        else:
            index_by_color[color] = len(color_groups)
            color_groups.append((color, list(polys), label))

    for shape in shapes:
        if shape.fill is not None and shape.closed:
            bucket(shape.fill, _shape_polygons(shape), shape.label or shape.kind)
        if options.stroke_as_satin and shape.stroke is not None and shape.stroke_width > 0:
            bucket(shape.stroke, _stroke_polygon(shape), f"{shape.label or shape.kind} stroke")

    if options.max_colors and len(color_groups) > options.max_colors:
        color_groups = _quantize_colors(color_groups, options.max_colors)

    # 2. Later colours sit on top; knock them out of the layers underneath so
    #    we do not stitch fabric-thick padding nobody will ever see.
    resolved: list[tuple[tuple[int, int, int], list[Polygon], str]] = []
    for i, (color, polys, label) in enumerate(color_groups):
        merged = union_polygons(polys)
        for _, upper_polys, _ in color_groups[i + 1 :]:
            upper = union_polygons(upper_polys)
            if not upper:
                continue
            next_merged: list[Polygon] = []
            for poly in merged:
                try:
                    diff = poly
                    for u in upper:
                        diff = diff.difference(u)
                    next_merged.extend(to_polygons(diff))
                except Exception:
                    next_merged.append(poly)
            merged = next_merged
        if merged:
            resolved.append((color, merged, label))

    # 3. Scale to the requested finished size.
    scale = _scale_factor(resolved, options)
    if scale != 1.0:
        resolved = [
            (
                color,
                [_scale_polygon(p, scale) for p in polys],
                label,
            )
            for color, polys, label in resolved
        ]

    # 4. Generate stitches.
    runs: list[StitchRun] = []
    objects: list[dict] = []
    for color_index, (color, polys, label) in enumerate(resolved):
        thread = catalog.match(color)
        angle = _fill_angle_for(color_index, options.fill_angle_deg, options.vary_fill_angle)
        for poly_index, poly in enumerate(polys):
            if poly.area <= 0:
                continue
            shape_runs, info = _plan_area(
                poly,
                thread,
                fabric,
                options,
                angle,
                f"{label or 'shape'} {poly_index + 1}",
                order_hint=color_index * 10,
            )
            runs.extend(shape_runs)
            info["color_index"] = color_index
            objects.append(info)

    pattern = assemble(
        runs,
        AssemblyParams(
            max_stitch=fabric.max_stitch,
            min_stitch=fabric.min_stitch,
            trim_threshold=mm_to_units(25.0),
            jump_threshold=mm_to_units(6.0),
            tie_in=options.tie_stitches,
            tie_off=options.tie_stitches,
        ),
    )

    if options.center_design:
        pattern.center_on_origin()

    issues = validate_pattern(
        pattern,
        hoop_width_mm=options.hoop_width_mm,
        hoop_height_mm=options.hoop_height_mm,
        max_stitches=options.max_stitches,
        max_colors=options.needle_count,
    )
    issues.extend(_craft_issues(pattern, objects, fabric))

    pattern.metadata.update(
        {
            "fabric": fabric.key,
            "fabric_name": fabric.name,
            "stabilizer": fabric.stabilizer,
            "needle": fabric.needle,
            "topping": fabric.topping,
            "speed_factor": fabric.speed_factor,
        }
    )

    return DigitizeResult(
        pattern=pattern,
        issues=issues,
        objects=objects,
        fabric=fabric.key,
        options={
            "fill_angle_deg": options.fill_angle_deg,
            "density_scale": options.density_scale,
            "auto_underlay": options.auto_underlay,
            "auto_satin": options.auto_satin,
            "thread_catalog": options.thread_catalog,
        },
    )


def digitize_svg(svg_source: str | bytes, options: DigitizeOptions | None = None) -> DigitizeResult:
    document: SvgDocument = parse_svg(svg_source)
    return digitize_shapes(document.shapes, options)


# ----------------------------------------------------------------- utilities
def _scale_factor(
    groups: list[tuple[tuple[int, int, int], list[Polygon], str]], options: DigitizeOptions
) -> float:
    if not (options.target_width_mm or options.target_height_mm):
        return 1.0
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for _, polys, _ in groups:
        for poly in polys:
            b = poly.bounds
            min_x, min_y = min(min_x, b[0]), min(min_y, b[1])
            max_x, max_y = max(max_x, b[2]), max(max_y, b[3])
    if min_x == float("inf") or max_x <= min_x or max_y <= min_y:
        return 1.0
    width, height = max_x - min_x, max_y - min_y
    factors: list[float] = []
    if options.target_width_mm:
        factors.append(mm_to_units(options.target_width_mm) / width)
    if options.target_height_mm:
        factors.append(mm_to_units(options.target_height_mm) / height)
    return min(factors) if factors else 1.0


def _scale_polygon(polygon: Polygon, factor: float) -> Polygon:
    return Polygon(
        [(x * factor, y * factor) for x, y in polygon.exterior.coords],
        [[(x * factor, y * factor) for x, y in ring.coords] for ring in polygon.interiors],
    )


def _craft_issues(
    pattern: EmbroideryPattern, objects: list[dict], fabric: FabricProfile
) -> list[dict]:
    """Digitizing-quality warnings a human digitizer would raise on review."""
    issues: list[dict] = []

    tiny = [o for o in objects if o.get("width_mm", 99) < fabric.min_letter_height_mm / 3.0]
    if tiny:
        issues.append({
            "level": "warning",
            "code": "small_detail",
            "message": f"{len(tiny)} element(s) are narrower than "
                       f"{fabric.min_letter_height_mm / 3.0:.1f} mm. On {fabric.name} these will "
                       "close up or disappear. Enlarge the design or simplify the detail.",
        })

    wide_satin = [
        o for o in objects
        if o.get("technique") == "satin" and o.get("width_mm", 0) > fabric.max_satin_width_mm
    ]
    if wide_satin:
        issues.append({
            "level": "warning",
            "code": "wide_satin",
            "message": f"{len(wide_satin)} satin column(s) exceed {fabric.max_satin_width_mm:.0f} mm. "
                       "Long stitches snag; these were split but a fill may sew better.",
        })

    if pattern.trim_count > max(20, pattern.color_count * 12):
        issues.append({
            "level": "info",
            "code": "many_trims",
            "message": f"{pattern.trim_count} trims. Consolidating small detached shapes "
                       "would cut run time noticeably.",
        })

    width_mm, height_mm = pattern.size_mm()
    if width_mm and height_mm and pattern.stitch_count:
        density = pattern.stitch_count / max(1.0, (width_mm / 10.0) * (height_mm / 10.0))
        if density < 28:
            issues.append({
                "level": "info",
                "code": "low_coverage",
                "message": "Coverage is light for the design area; the fabric will show "
                           "through. Tighten density if you want a solid look.",
            })
    return issues


def estimate_runtime_minutes(
    stitch_count: int, color_changes: int, trims: int, speed_spm: int = 800,
    speed_factor: float = 1.0,
) -> float:
    """Realistic sew time: stitches at the derated head speed, plus the fixed
    overhead of colour changes and trims that quotes always forget."""
    effective = max(150.0, speed_spm * speed_factor)
    sewing = stitch_count / effective
    color_overhead = color_changes * 0.35    # ~21 s per change including re-start
    trim_overhead = trims * 0.02             # ~1.2 s per trim
    return round(sewing + color_overhead + trim_overhead, 2)
