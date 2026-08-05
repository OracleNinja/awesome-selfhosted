"""Text designs (lettering).

Lettering is rendered to a high-resolution bitmap with the requested font, then
traced to outlines and handed to the digitizer, which recognises the strokes as
narrow columns and satins them — the same route a human digitizer takes when
they convert type to outlines.

Rendering at 8x the working resolution keeps the traced curves smooth; below
about 4x you can see the pixel staircase in the finished stitching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .raster import TraceOptions, trace_image
from .svg_parse import SvgShape

log = logging.getLogger(__name__)

RENDER_SCALE = 8
DEFAULT_HEIGHT_MM = 25.0

FONT_SEARCH_PATHS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/msttcorefonts",
    "/usr/share/fonts/TTF",
    "/usr/share/fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "C:/Windows/Fonts",
]

# Curated set: the faces that actually embroider well. Heavy, low-contrast,
# generously spaced letterforms survive the pull of thread; hairline serifs and
# thin scripts do not.
BUILTIN_FONTS = [
    {"key": "sans_bold", "name": "Block Bold", "files": ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arialbd.ttf"], "notes": "Best all-round choice for names and small text."},
    {"key": "sans", "name": "Block Regular", "files": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"], "notes": "Use at 8 mm cap height or larger."},
    {"key": "serif_bold", "name": "Serif Bold", "files": ["DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf", "timesbd.ttf"], "notes": "Classic monogram look; keep serifs above 1 mm."},
    {"key": "serif", "name": "Serif Regular", "files": ["DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "times.ttf"], "notes": "Large sizes only — thin serifs drop out."},
    {"key": "mono_bold", "name": "Mono Bold", "files": ["DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf"], "notes": "Even stroke weight; very reliable on caps."},
]


@dataclass
class TextOptions:
    text: str
    font_key: str = "sans_bold"
    font_path: str | None = None
    height_mm: float = DEFAULT_HEIGHT_MM      # cap height of a single line
    letter_spacing: float = 0.0               # extra tracking, in em units
    line_spacing: float = 1.25
    color: tuple[int, int, int] = (0, 0, 0)
    align: str = "center"                     # left | center | right
    arc_degrees: float = 0.0                  # >0 bends the baseline upward


def find_font(font_key: str = "sans_bold", explicit_path: str | None = None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        if path.exists():
            return path
        raise FileNotFoundError(f"Font not found: {explicit_path}")

    entry = next((f for f in BUILTIN_FONTS if f["key"] == font_key), BUILTIN_FONTS[0])
    candidates = entry["files"] + [f["files"][0] for f in BUILTIN_FONTS]
    for directory in FONT_SEARCH_PATHS:
        base = Path(directory)
        if not base.exists():
            continue
        for filename in candidates:
            direct = base / filename
            if direct.exists():
                return direct
        for filename in candidates:
            matches = list(base.rglob(filename))
            if matches:
                return matches[0]
    raise FileNotFoundError(
        "No usable TrueType font was found on this host. Install a font package "
        "(on Debian/Ubuntu: `apt-get install fonts-dejavu-core`) or upload a .ttf "
        "with the request."
    )


def available_fonts() -> list[dict]:
    out = []
    for entry in BUILTIN_FONTS:
        try:
            path = find_font(entry["key"])
            available = True
            resolved = path.name
        except FileNotFoundError:
            available = False
            resolved = ""
        out.append({
            "key": entry["key"],
            "name": entry["name"],
            "notes": entry["notes"],
            "available": available,
            "file": resolved,
        })
    return out


def _render_text_bitmap(options: TextOptions) -> Image.Image:
    """Render the lettering as black-on-white at high resolution."""
    font_path = find_font(options.font_key, options.font_path)
    lines = [line for line in options.text.splitlines() if line.strip()] or [options.text]

    # Render at RENDER_SCALE px per 0.25 mm so the traced curves stay smooth.
    px_per_mm = RENDER_SCALE * 4.0
    target_px = max(24, int(options.height_mm * px_per_mm))

    font = ImageFont.truetype(str(font_path), target_px)
    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * options.line_spacing)

    tracking = int(target_px * options.letter_spacing)

    def line_width(text: str) -> int:
        width = int(font.getlength(text))
        return width + tracking * max(0, len(text) - 1)

    widths = [line_width(line) for line in lines]
    max_width = max(widths) if widths else 1
    pad = int(target_px * 0.25)
    canvas_w = max_width + pad * 2
    canvas_h = line_height * len(lines) + pad * 2

    image = Image.new("L", (canvas_w, canvas_h), 255)
    draw = ImageDraw.Draw(image)

    for index, line in enumerate(lines):
        if options.align == "left":
            x = pad
        elif options.align == "right":
            x = canvas_w - pad - widths[index]
        else:
            x = (canvas_w - widths[index]) // 2
        y = pad + index * line_height
        if tracking:
            cursor = x
            for char in line:
                draw.text((cursor, y), char, font=font, fill=0)
                cursor += int(font.getlength(char)) + tracking
        else:
            draw.text((x, y), line, font=font, fill=0)

    return image


def _apply_arc(image: Image.Image, degrees: float) -> Image.Image:
    """Bend the baseline into an arc — the classic cap/left-chest treatment."""
    if abs(degrees) < 1:
        return image
    import math

    import numpy as np

    source = np.array(image)
    h, w = source.shape
    radius = w / math.radians(abs(degrees))
    out_h = int(h + radius - radius * math.cos(math.radians(abs(degrees)) / 2)) + 4
    out = np.full((out_h, w), 255, dtype=np.uint8)

    sign = 1 if degrees > 0 else -1
    for x in range(w):
        theta = (x - w / 2) / radius
        offset = int(sign * (radius - radius * math.cos(theta)))
        for y in range(h):
            ty = y + (offset if sign > 0 else out_h - h + offset)
            if 0 <= ty < out_h:
                out[ty, x] = min(out[ty, x], source[y, x])
    return Image.fromarray(out)


def text_to_shapes(options: TextOptions) -> list[SvgShape]:
    """Render lettering and trace it into stitchable outlines."""
    if not options.text.strip():
        raise ValueError("Text design requires some text.")

    bitmap = _render_text_bitmap(options)
    bitmap = _apply_arc(bitmap, options.arc_degrees)

    import io

    buffer = io.BytesIO()
    bitmap.convert("RGB").save(buffer, format="PNG")

    lines = max(1, len([line for line in options.text.splitlines() if line.strip()]))
    # Trace at the true finished size so stroke widths are physically correct.
    target_width_mm = bitmap.width / (RENDER_SCALE * 4.0)

    result = trace_image(
        buffer.getvalue(),
        TraceOptions(
            max_colors=2,
            remove_background=True,
            background_tolerance=40,
            smooth=0.3,
            simplify_tolerance=1.0,
            min_area_px=40,
            despeckle=1,
            target_width_mm=target_width_mm,
            use_potrace=True,
        ),
    )

    # Trace returns light-to-dark; the lettering is the dark layer.
    shapes = [s for s in result.shapes if s.fill and sum(s.fill) < 400]
    if not shapes:
        shapes = result.shapes
    for shape in shapes:
        shape.fill = options.color
        shape.label = "lettering"
    log.debug("Rendered %d lettering shape(s) across %d line(s)", len(shapes), lines)
    return shapes


def minimum_height_for_font(font_key: str, fabric_min_mm: float) -> float:
    """Smallest sensible cap height for a face on a given fabric."""
    penalty = {"serif": 1.6, "serif_bold": 1.2, "sans": 1.15, "sans_bold": 1.0, "mono_bold": 1.0}
    return round(fabric_min_mm * penalty.get(font_key, 1.2), 1)
