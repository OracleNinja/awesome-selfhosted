"""Raster and vector previews of a stitched pattern.

The PNG renderer draws each stitch as a short capsule with a highlight and a
shadow, which is what makes embroidery read as thread rather than as line art.
The SVG renderer emits one polyline per colour block for the browser-side
studio, which animates it stitch by stitch.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

from .pattern import Command, EmbroideryPattern

FABRIC_SWATCHES = {
    "white": (245, 245, 243),
    "natural": (232, 226, 212),
    "grey": (150, 153, 156),
    "navy": (28, 42, 68),
    "black": (26, 26, 28),
    "red": (150, 33, 38),
    "forest": (32, 66, 46),
    "royal": (26, 62, 140),
}


@dataclass(slots=True)
class RenderOptions:
    width: int = 1200
    height: int = 1200
    padding: int = 48
    background: str = "white"
    show_jumps: bool = False
    thread_width_mm: float = 0.4
    fabric_texture: bool = True
    shadow: bool = True


def _fabric_background(size: tuple[int, int], color: tuple[int, int, int], texture: bool) -> Image.Image:
    image = Image.new("RGB", size, color)
    if not texture:
        return image
    # A cheap woven texture: alternating 3px warp/weft lines at very low
    # contrast. Enough to stop the preview looking like clip art.
    draw = ImageDraw.Draw(image, "RGBA")
    dark = (0, 0, 0, 10)
    light = (255, 255, 255, 12)
    for x in range(0, size[0], 4):
        draw.line([(x, 0), (x, size[1])], fill=dark if (x // 4) % 2 else light, width=1)
    for y in range(0, size[1], 4):
        draw.line([(0, y), (size[0], y)], fill=light if (y // 4) % 2 else dark, width=1)
    return image


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in color)  # type: ignore[return-value]


def render_png(pattern: EmbroideryPattern, options: RenderOptions | None = None) -> bytes:
    options = options or RenderOptions()
    min_x, min_y, max_x, max_y = pattern.bounds()
    design_w = max(1.0, max_x - min_x)
    design_h = max(1.0, max_y - min_y)

    usable_w = options.width - options.padding * 2
    usable_h = options.height - options.padding * 2
    scale = min(usable_w / design_w, usable_h / design_h)

    offset_x = options.padding + (usable_w - design_w * scale) / 2.0 - min_x * scale
    offset_y = options.padding + (usable_h - design_h * scale) / 2.0 - min_y * scale

    bg = FABRIC_SWATCHES.get(options.background, FABRIC_SWATCHES["white"])
    image = _fabric_background((options.width, options.height), bg, options.fabric_texture)

    thread_px = max(1.5, options.thread_width_mm * 10.0 * scale)

    def project(x: float, y: float) -> tuple[float, float]:
        return (x * scale + offset_x, y * scale + offset_y)

    # Shadow pass gives the stitching physical relief against the fabric.
    if options.shadow:
        shadow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        for block in pattern.blocks:
            prev = None
            for stitch in block.stitches:
                point = project(stitch.x, stitch.y)
                if prev is not None and stitch.command == Command.STITCH:
                    shadow_draw.line(
                        [(prev[0] + 2, prev[1] + 3), (point[0] + 2, point[1] + 3)],
                        fill=(0, 0, 0, 70),
                        width=int(thread_px * 1.3),
                        joint="curve",
                    )
                prev = point if stitch.command != Command.TRIM else None
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(2.2))
        image = Image.alpha_composite(image.convert("RGBA"), shadow_layer).convert("RGB")

    draw = ImageDraw.Draw(image, "RGBA")

    for block in pattern.blocks:
        base = block.thread.rgb
        core = base
        highlight = _shade(base, 1.45)
        lowlight = _shade(base, 0.62)
        prev = None
        for stitch in block.stitches:
            point = project(stitch.x, stitch.y)
            if prev is not None:
                if stitch.command == Command.STITCH:
                    draw.line([prev, point], fill=lowlight, width=int(thread_px * 1.25) or 1)
                    draw.line([prev, point], fill=core, width=int(thread_px) or 1)
                    # Offset specular streak along the thread body.
                    dx, dy = point[0] - prev[0], point[1] - prev[1]
                    length = math.hypot(dx, dy)
                    if length > 0.5:
                        nx, ny = -dy / length, dx / length
                        shift = thread_px * 0.22
                        draw.line(
                            [
                                (prev[0] + nx * shift, prev[1] + ny * shift),
                                (point[0] + nx * shift, point[1] + ny * shift),
                            ],
                            fill=(*highlight, 170),
                            width=max(1, int(thread_px * 0.34)),
                        )
                elif options.show_jumps and stitch.command in (Command.JUMP, Command.TRIM):
                    draw.line([prev, point], fill=(255, 255, 255, 90), width=1)
            prev = point

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_svg(pattern: EmbroideryPattern, stroke_mm: float = 0.4) -> str:
    """Lightweight vector preview — one polyline per colour block."""
    min_x, min_y, max_x, max_y = pattern.bounds()
    pad = 20.0
    width = max(1.0, max_x - min_x) + pad * 2
    height = max(1.0, max_y - min_y) + pad * 2
    stroke = stroke_mm * 10.0

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x - pad:.1f} {min_y - pad:.1f} '
        f'{width:.1f} {height:.1f}" width="100%" height="100%">',
        '<g fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]
    for block in pattern.blocks:
        runs: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        for stitch in block.stitches:
            if stitch.command == Command.STITCH:
                current.append((stitch.x, stitch.y))
            else:
                if len(current) > 1:
                    runs.append(current)
                current = []
        if len(current) > 1:
            runs.append(current)
        for run in runs:
            points = " ".join(f"{x:.1f},{y:.1f}" for x, y in run)
            parts.append(
                f'<polyline points="{points}" stroke="{block.thread.hex}" '
                f'stroke-width="{stroke:.1f}"/>'
            )
    parts.append("</g></svg>")
    return "".join(parts)


def stitch_stream(pattern: EmbroideryPattern, max_points: int | None = None) -> dict:
    """Compact JSON payload for the browser stitch simulator.

    Coordinates stay in internal units; the client scales them.  Optionally
    decimated so a 200k-stitch design does not ship megabytes of JSON.
    """
    min_x, min_y, max_x, max_y = pattern.bounds()
    total = sum(len(b.stitches) for b in pattern.blocks)
    step = 1
    if max_points and total > max_points:
        step = math.ceil(total / max_points)

    blocks = []
    for block in pattern.blocks:
        coords: list[float] = []
        flags: list[int] = []
        for index, stitch in enumerate(block.stitches):
            if step > 1 and index % step and stitch.command == Command.STITCH:
                continue
            coords.append(round(stitch.x, 1))
            coords.append(round(stitch.y, 1))
            flags.append(int(stitch.command))
        blocks.append(
            {
                "color": block.thread.hex,
                "name": block.thread.name or block.thread.hex,
                "code": block.thread.code,
                "technique": block.technique,
                "label": block.label,
                "coords": coords,
                "flags": flags,
            }
        )

    return {
        "bounds": {"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y},
        "decimation": step,
        "blocks": blocks,
        "summary": pattern.summary(),
    }
