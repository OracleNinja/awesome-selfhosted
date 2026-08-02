"""Digitizing engine tests.

These assert the properties that matter for a file that actually sews:
stitch lengths inside machine limits, geometry preserved through the format
writers, density in the right ballpark, and fabric profiles changing the
output in the direction a digitizer would expect.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Polygon

from app.embroidery import (
    DigitizeOptions,
    build_package,
    digitize_svg,
    get_fabric,
    render_png,
    render_svg,
    stitch_stream,
    write,
)
from app.embroidery.fills import FillParams, generate_fill
from app.embroidery.geometry import distance, resample_path, simplify_ring
from app.embroidery.optimizer import clamp_stitch_lengths
from app.embroidery.pattern import Command, EmbroideryPattern, Thread
from app.embroidery.satin import SatinParams, satin_from_ring
from app.embroidery.svg_parse import parse_svg
from app.embroidery.threads import GENIE_STANDARD, color_distance
from app.embroidery.units import mm_to_units, units_to_mm
from app.embroidery.writers import EXPORTABLE, write as write_format


# ------------------------------------------------------------------- units
def test_unit_round_trip():
    assert units_to_mm(mm_to_units(25.4)) == pytest.approx(25.4)
    assert mm_to_units(1.0) == 10.0


# ------------------------------------------------------------------- parsing
def test_svg_parse_extracts_all_shapes(sample_svg):
    document = parse_svg(sample_svg)
    kinds = sorted(shape.kind for shape in document.shapes)
    assert kinds == ["circle", "path", "path", "rect"]
    # 60 mm wide artwork -> 600 internal units.
    assert document.width == pytest.approx(600, abs=1)


def test_svg_parse_handles_transforms():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"
        viewBox="0 0 100 100">
      <g transform="translate(10,10) scale(2)">
        <rect x="0" y="0" width="10" height="10" fill="#FF0000"/>
      </g>
    </svg>"""
    document = parse_svg(svg)
    assert len(document.shapes) == 1
    xs = [p[0] for p in document.shapes[0].outer]
    # translate(10) then scale(2): 0..10 becomes 10..30 user units.
    assert min(xs) == pytest.approx(10.0, abs=0.5)
    assert max(xs) == pytest.approx(30.0, abs=0.5)


def test_svg_parse_bezier_flattening():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"
        viewBox="0 0 100 100">
      <path d="M10 50 C 10 10, 90 10, 90 50 Z" fill="#000"/>
    </svg>"""
    document = parse_svg(svg)
    # A curve must flatten to many points, not just the two endpoints.
    assert len(document.shapes[0].outer) > 8


# ---------------------------------------------------------------- geometry
def test_simplify_preserves_shape():
    circle = [
        (50 + 40 * math.cos(t / 100 * 2 * math.pi), 50 + 40 * math.sin(t / 100 * 2 * math.pi))
        for t in range(100)
    ]
    simplified = simplify_ring(circle, 2.0)
    assert 8 < len(simplified) < len(circle)
    assert Polygon(simplified).area == pytest.approx(Polygon(circle).area, rel=0.05)


def test_resample_respects_step():
    path = [(0.0, 0.0), (100.0, 0.0)]
    points = resample_path(path, 25.0)
    gaps = [distance(points[i], points[i + 1]) for i in range(len(points) - 1)]
    assert all(gap <= 25.1 for gap in gaps)


# ------------------------------------------------------------------- fills
def test_fill_covers_polygon_at_requested_density():
    square = Polygon([(0, 0), (200, 0), (200, 200), (0, 200)])   # 20 x 20 mm
    runs = generate_fill(square, FillParams(spacing=4.0, angle_deg=0.0, max_stitch=40.0))
    assert runs, "fill produced no stitches"
    points = [p for run in runs for p in run]
    ys = sorted({round(p[1]) for p in points})
    # 200 units tall at 4-unit spacing -> ~50 rows.
    assert 45 <= len(ys) <= 55


def test_fill_handles_holes():
    donut = Polygon(
        [(0, 0), (200, 0), (200, 200), (0, 200)],
        [[(80, 80), (120, 80), (120, 120), (80, 120)]],
    )
    runs = generate_fill(donut, FillParams(spacing=8.0, angle_deg=0.0, max_stitch=40.0))
    points = [p for run in runs for p in run]
    # No stitch should land in the middle of the hole.
    inside_hole = [p for p in points if 85 < p[0] < 115 and 85 < p[1] < 115]
    assert not inside_hole


def test_fill_stagger_avoids_a_split_line():
    square = Polygon([(0, 0), (400, 0), (400, 200), (0, 200)])
    runs = generate_fill(
        square, FillParams(spacing=4.0, angle_deg=0.0, max_stitch=40.0, stagger=4)
    )
    points = [p for run in runs for p in run]
    # Interior penetrations must not all share the same x, which is exactly
    # what causes a visible seam down a tatami fill.
    interior_x = {round(p[0]) for p in points if 40 < p[0] < 360}
    assert len(interior_x) > 4


# ------------------------------------------------------------------ satin
def test_satin_alternates_between_rails():
    column = [(0.0, 0.0), (200.0, 0.0), (200.0, 30.0), (0.0, 30.0)]
    stitches = satin_from_ring(column, SatinParams(spacing=4.0, pull_compensation=0.0))
    assert len(stitches) > 20
    ys = [p[1] for p in stitches]
    # A satin zigzags across the column: both edges must be visited.
    assert min(ys) < 5 and max(ys) > 25


def test_satin_pull_compensation_widens_the_column():
    column = [(0.0, 0.0), (200.0, 0.0), (200.0, 30.0), (0.0, 30.0)]
    plain = satin_from_ring(column, SatinParams(spacing=4.0, pull_compensation=0.0))
    compensated = satin_from_ring(column, SatinParams(spacing=4.0, pull_compensation=3.0))
    span_plain = max(p[1] for p in plain) - min(p[1] for p in plain)
    span_comp = max(p[1] for p in compensated) - min(p[1] for p in compensated)
    assert span_comp > span_plain


# -------------------------------------------------------------- optimizer
def test_clamp_splits_over_long_stitches():
    points = [(0.0, 0.0), (500.0, 0.0)]
    clamped = clamp_stitch_lengths(points, max_stitch=120.0, min_stitch=6.0)
    gaps = [distance(clamped[i], clamped[i + 1]) for i in range(len(clamped) - 1)]
    assert all(gap <= 120.1 for gap in gaps)


def test_clamp_drops_degenerate_stitches():
    points = [(0.0, 0.0), (0.1, 0.0), (100.0, 0.0)]
    clamped = clamp_stitch_lengths(points, max_stitch=120.0, min_stitch=6.0)
    assert len(clamped) == 2


# ------------------------------------------------------------- digitizing
def test_digitize_produces_a_sewable_pattern(sample_svg):
    result = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80))
    pattern = result.pattern

    assert pattern.stitch_count > 500
    assert pattern.color_count == 4

    width_mm, height_mm = pattern.size_mm()
    assert width_mm == pytest.approx(80, abs=2)

    fabric = get_fabric("cotton_shirt")
    for block in pattern.blocks:
        previous = None
        for stitch in block.stitches:
            if previous is not None and stitch.command == Command.STITCH:
                gap = distance((previous.x, previous.y), (stitch.x, stitch.y))
                assert gap <= fabric.max_stitch + 1, "stitch exceeds the machine limit"
            previous = stitch


def test_digitize_density_is_in_the_commercial_range(sample_svg):
    result = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80))
    width_mm, height_mm = result.pattern.size_mm()
    area_cm2 = (width_mm / 10) * (height_mm / 10)
    density = result.pattern.stitch_count / area_cm2
    # Tatami at 0.4 mm rows / 4 mm stitches sits near 60 penetrations per cm².
    assert 20 < density < 180, f"density {density:.0f}/cm² is outside the sane range"


def test_fabric_profile_changes_the_result(sample_svg):
    cotton = digitize_svg(sample_svg, DigitizeOptions(fabric="cotton_shirt",
                                                     target_width_mm=80)).pattern
    beanie = digitize_svg(sample_svg, DigitizeOptions(fabric="beanie",
                                                     target_width_mm=80)).pattern
    # A beanie gets three underlay passes against cotton's single edge run,
    # so it must cost meaningfully more stitches.
    assert beanie.stitch_count > cotton.stitch_count


def test_target_size_is_honoured(sample_svg):
    for target in (40.0, 120.0):
        pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=target)).pattern
        assert pattern.size_mm()[0] == pytest.approx(target, abs=2)


def test_max_colors_reduces_the_palette(sample_svg):
    result = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80, max_colors=2))
    assert result.pattern.color_count <= 2


def test_hoop_validation_flags_oversize_designs(sample_svg):
    result = digitize_svg(
        sample_svg,
        DigitizeOptions(target_width_mm=300, hoop_width_mm=100, hoop_height_mm=100),
    )
    codes = {issue["code"] for issue in result.issues}
    assert "hoop_width" in codes


def test_pattern_is_centred_by_default(sample_svg):
    pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80)).pattern
    min_x, min_y, max_x, max_y = pattern.bounds()
    assert abs(min_x + max_x) < 2
    assert abs(min_y + max_y) < 2


# ------------------------------------------------------------------ threads
def test_thread_matching_picks_a_perceptually_close_colour():
    thread = GENIE_STANDARD.match((18, 87, 166))
    assert color_distance(thread.rgb, (18, 87, 166)) < 12


def test_thread_catalog_import_from_csv():
    from app.embroidery.threads import ThreadCatalog

    catalog = ThreadCatalog.from_csv(
        "Test", "code,name,hex\n1902,Ivory,#F2E8D5\n1755,Sky,#7EBBE3\n"
    )
    assert len(catalog) == 2
    assert catalog.match((240, 232, 213)).code == "1902"


# ------------------------------------------------------------------ writers
@pytest.mark.parametrize("extension", sorted(EXPORTABLE))
def test_every_exportable_format_writes_bytes(sample_svg, extension):
    pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80)).pattern
    data = write_format(pattern, extension)
    assert len(data) > 100, f".{extension} produced a suspiciously small file"


def test_hus_export_is_refused_with_an_explanation(sample_svg):
    from app.embroidery.writers import UnsupportedFormatError

    pattern = digitize_svg(sample_svg, DigitizeOptions()).pattern
    with pytest.raises(UnsupportedFormatError, match="VP3"):
        write_format(pattern, "hus")


def test_native_dst_header_is_well_formed(sample_svg):
    pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80)).pattern
    data = write(pattern, "dst", prefer_native=True)
    assert data[:3] == b"LA:"
    assert len(data) > 512
    # The 512-byte header is followed by whole 3-byte stitch records.
    assert (len(data) - 512) % 3 == 0
    assert data[-3:] == b"\x00\x00\xf3"      # end-of-file record


def test_native_dst_round_trips_through_an_independent_reader(sample_svg):
    """Our hand-written DST encoder must agree with a third-party decoder."""
    pyembroidery = pytest.importorskip("pyembroidery")
    import os
    import tempfile

    pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80)).pattern
    data = write(pattern, "dst", prefer_native=True)

    handle, path = tempfile.mkstemp(suffix=".dst")
    os.close(handle)
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        decoded = pyembroidery.read(path)
    finally:
        os.unlink(path)

    xs = [s[0] for s in decoded.stitches]
    ys = [s[1] for s in decoded.stitches]
    expected = pattern.bounds()

    # Geometry must survive the encode/decode cycle to within rounding.
    assert min(xs) == pytest.approx(expected[0], abs=2)
    assert max(xs) == pytest.approx(expected[2], abs=2)
    assert max(ys) - min(ys) == pytest.approx(expected[3] - expected[1], abs=2)

    stitches = sum(1 for s in decoded.stitches if s[2] == pyembroidery.STITCH)
    assert stitches == pytest.approx(pattern.stitch_count, rel=0.02)


# ----------------------------------------------------------------- rendering
def test_render_png_and_svg(sample_svg):
    pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80)).pattern
    png = render_png(pattern)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    svg = render_svg(pattern)
    assert svg.startswith("<svg") and "polyline" in svg


def test_stitch_stream_decimates_large_patterns():
    pattern = EmbroideryPattern(name="big")
    block = pattern.new_block(Thread(0, 0, 0))
    for i in range(5000):
        block.add(float(i), float(i % 100))
    stream = stitch_stream(pattern, max_points=500)
    assert stream["decimation"] > 1
    assert len(stream["blocks"][0]["coords"]) // 2 < 1200


# ------------------------------------------------------------------ package
def test_export_package_contains_the_production_set(sample_svg):
    import io
    import zipfile

    result = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=80))
    data, manifest = build_package(
        result.pattern, "Test Logo", ["dst", "pes"], "cotton_shirt",
        issues=result.issues,
    )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())

    assert "Test_Logo.dst" in names
    assert "Test_Logo.pes" in names
    assert {"preview.png", "thread_chart.pdf", "production_notes.pdf",
            "manifest.json"} <= names
    assert manifest["summary"]["stitch_count"] == result.pattern.stitch_count
    assert not manifest["warnings"]


# ------------------------------------------------------------------- raster
def test_trace_image_finds_the_colour_layers(sample_png):
    from app.embroidery import TraceOptions, trace_image

    result = trace_image(sample_png, TraceOptions(max_colors=4, target_width_mm=80))
    assert len(result.shapes) >= 3
    assert result.background_removed


def test_digitize_a_traced_raster(sample_png):
    from app.embroidery import TraceOptions, digitize_shapes, trace_image

    trace = trace_image(sample_png, TraceOptions(max_colors=4, target_width_mm=80))
    result = digitize_shapes(trace.shapes, DigitizeOptions(target_width_mm=80))
    assert result.pattern.stitch_count > 300


# -------------------------------------------------------------------- text
def test_text_design_becomes_satin_lettering():
    from app.embroidery import TextOptions, digitize_shapes, text_to_shapes
    from app.embroidery.text import find_font

    try:
        find_font()
    except FileNotFoundError:
        pytest.skip("No TrueType font installed on this host")

    shapes = text_to_shapes(TextOptions(text="GENIE", font_key="sans_bold", height_mm=25))
    assert shapes
    result = digitize_shapes(shapes, DigitizeOptions(fabric="cotton_shirt"))
    assert result.pattern.stitch_count > 200
    techniques = {block.technique for block in result.pattern.blocks}
    assert techniques & {"satin", "fill"}
