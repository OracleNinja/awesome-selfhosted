"""Module 10 — Customer mockup generator.

Renders the stitched design onto a garment so a customer can approve placement
and size before anything is sewn.  Garments are drawn procedurally rather than
composited onto stock photography: no licensing exposure, every colourway is
available instantly, and the placement rectangle is geometrically exact, which
is the part that actually matters for approval.

Each template carries a real placement window in millimetres, so the preview
shows the design at true relative scale — a 50 mm left chest looks like a
50 mm left chest, not "whatever filled the box".
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

from app.embroidery.pattern import EmbroideryPattern
from app.embroidery.render import RenderOptions, render_png

CANVAS = 1400


@dataclass(frozen=True)
class GarmentTemplate:
    key: str
    name: str
    fabric_profile: str
    # Placement window in canvas coordinates (x, y, w, h) and its real width.
    placement: tuple[float, float, float, float]
    placement_width_mm: float
    placement_name: str
    default_color: str = "#1F2937"
    garment_width_mm: float = 560.0


TEMPLATES: dict[str, GarmentTemplate] = {
    t.key: t
    for t in [
        GarmentTemplate("tshirt_left_chest", "T-Shirt — Left Chest", "cotton_shirt",
                        (0.565, 0.335, 0.16, 0.16), 89.0, "Left chest"),
        GarmentTemplate("tshirt_full_front", "T-Shirt — Full Front", "cotton_shirt",
                        (0.33, 0.36, 0.34, 0.30), 254.0, "Full front"),
        GarmentTemplate("hoodie_front", "Hoodie — Left Chest", "hoodie",
                        (0.575, 0.375, 0.15, 0.15), 89.0, "Left chest", "#111827"),
        GarmentTemplate("cap_front", "Cap — Front Panel", "hat",
                        (0.345, 0.395, 0.31, 0.135), 115.0, "Cap front", "#0F172A", 300.0),
        GarmentTemplate("beanie_cuff", "Beanie — Cuff", "beanie",
                        (0.36, 0.63, 0.28, 0.10), 90.0, "Cuff", "#334155", 260.0),
        GarmentTemplate("jacket_back", "Jacket — Back Yoke", "canvas",
                        (0.28, 0.30, 0.44, 0.18), 280.0, "Back yoke", "#1E293B"),
        GarmentTemplate("tote_front", "Tote Bag", "canvas",
                        (0.31, 0.40, 0.38, 0.30), 240.0, "Centre front", "#D6CFC0", 400.0),
        GarmentTemplate("polo_left_chest", "Polo — Left Chest", "polyester_shirt",
                        (0.575, 0.365, 0.14, 0.14), 76.0, "Left chest", "#334155"),
    ]
}

FABRIC_COLORS = {
    "white": "#F4F4F2", "natural": "#E6DFCE", "ash": "#C9CBCC", "sport_grey": "#A9ADB0",
    "charcoal": "#3F4448", "black": "#141416", "navy": "#1B2A47", "royal": "#1E3FA0",
    "red": "#9E2126", "maroon": "#5C1A22", "forest": "#1F4632", "olive": "#4B5320",
    "sand": "#CBB994", "orange": "#C2571B", "purple": "#4B2A72", "pink": "#D98BA5",
}


def _shade(hex_color: str, factor: float) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return tuple(max(0, min(255, int(c * factor))) for c in (r, g, b))  # type: ignore[return-value]


def _draw_tshirt(draw: ImageDraw.ImageDraw, color: str, hoodie: bool = False) -> None:
    w = h = CANVAS
    body = _shade(color, 1.0)
    shadow = _shade(color, 0.82)
    highlight = _shade(color, 1.12)

    torso = [
        (0.30 * w, 0.24 * h), (0.38 * w, 0.19 * h), (0.44 * w, 0.22 * h),
        (0.50 * w, 0.24 * h), (0.56 * w, 0.22 * h), (0.62 * w, 0.19 * h),
        (0.70 * w, 0.24 * h), (0.78 * w, 0.38 * h), (0.70 * w, 0.43 * h),
        (0.71 * w, 0.86 * h), (0.29 * w, 0.86 * h), (0.30 * w, 0.43 * h),
        (0.22 * w, 0.38 * h),
    ]
    draw.polygon(torso, fill=body)

    # Sleeve seams and a soft side shadow give the flat shape some volume.
    draw.line([(0.30 * w, 0.43 * h), (0.30 * w, 0.86 * h)], fill=shadow, width=4)
    draw.line([(0.70 * w, 0.43 * h), (0.70 * w, 0.86 * h)], fill=shadow, width=4)
    draw.polygon(
        [(0.29 * w, 0.43 * h), (0.36 * w, 0.44 * h), (0.36 * w, 0.86 * h), (0.29 * w, 0.86 * h)],
        fill=shadow,
    )
    draw.polygon(
        [(0.64 * w, 0.44 * h), (0.71 * w, 0.43 * h), (0.71 * w, 0.86 * h), (0.64 * w, 0.86 * h)],
        fill=highlight,
    )

    # Collar.
    draw.ellipse(
        [0.435 * w, 0.185 * h, 0.565 * w, 0.255 * h], fill=_shade(color, 0.72)
    )
    draw.ellipse([0.445 * w, 0.19 * h, 0.555 * w, 0.245 * h], fill=(250, 250, 250))

    if hoodie:
        draw.ellipse([0.40 * w, 0.15 * h, 0.60 * w, 0.30 * h], outline=shadow, width=10)
        draw.line([(0.47 * w, 0.26 * h), (0.47 * w, 0.42 * h)], fill=(240, 240, 240), width=6)
        draw.line([(0.53 * w, 0.26 * h), (0.53 * w, 0.42 * h)], fill=(240, 240, 240), width=6)
        draw.rectangle([0.36 * w, 0.60 * h, 0.64 * w, 0.74 * h], outline=shadow, width=5)
    draw.rectangle([0.29 * w, 0.83 * h, 0.71 * w, 0.86 * h], fill=shadow)


def _draw_cap(draw: ImageDraw.ImageDraw, color: str) -> None:
    w = h = CANVAS
    body = _shade(color, 1.0)
    shadow = _shade(color, 0.78)
    draw.pieslice([0.24 * w, 0.22 * h, 0.76 * w, 0.74 * h], 180, 360, fill=body)
    draw.rectangle([0.24 * w, 0.47 * h, 0.76 * w, 0.56 * h], fill=body)
    # Brim.
    draw.pieslice([0.18 * w, 0.50 * h, 0.82 * w, 0.72 * h], 0, 180, fill=shadow)
    # Panel seams.
    for x in (0.38, 0.50, 0.62):
        draw.line([(x * w, 0.23 * h), (x * w, 0.55 * h)], fill=shadow, width=3)
    draw.ellipse([0.485 * w, 0.205 * h, 0.515 * w, 0.235 * h], fill=shadow)
    draw.rectangle([0.24 * w, 0.52 * h, 0.76 * w, 0.56 * h], fill=_shade(color, 0.88))


def _draw_beanie(draw: ImageDraw.ImageDraw, color: str) -> None:
    w = h = CANVAS
    body = _shade(color, 1.0)
    shadow = _shade(color, 0.8)
    draw.pieslice([0.28 * w, 0.20 * h, 0.72 * w, 0.78 * h], 180, 360, fill=body)
    draw.rectangle([0.28 * w, 0.49 * h, 0.72 * w, 0.62 * h], fill=body)
    draw.rectangle([0.27 * w, 0.60 * h, 0.73 * w, 0.76 * h], fill=shadow)
    for i in range(14):
        x = (0.28 + i * 0.032) * w
        draw.line([(x, 0.60 * h), (x, 0.76 * h)], fill=_shade(color, 0.72), width=3)


def _draw_jacket(draw: ImageDraw.ImageDraw, color: str) -> None:
    w = h = CANVAS
    body = _shade(color, 1.0)
    shadow = _shade(color, 0.8)
    draw.polygon(
        [
            (0.26 * w, 0.22 * h), (0.74 * w, 0.22 * h), (0.82 * w, 0.40 * h),
            (0.74 * w, 0.44 * h), (0.74 * w, 0.84 * h), (0.26 * w, 0.84 * h),
            (0.26 * w, 0.44 * h), (0.18 * w, 0.40 * h),
        ],
        fill=body,
    )
    draw.rectangle([0.40 * w, 0.19 * h, 0.60 * w, 0.25 * h], fill=shadow)
    draw.rectangle([0.26 * w, 0.80 * h, 0.74 * w, 0.84 * h], fill=shadow)
    draw.line([(0.26 * w, 0.44 * h), (0.26 * w, 0.84 * h)], fill=shadow, width=5)
    draw.line([(0.74 * w, 0.44 * h), (0.74 * w, 0.84 * h)], fill=shadow, width=5)


def _draw_tote(draw: ImageDraw.ImageDraw, color: str) -> None:
    w = h = CANVAS
    body = _shade(color, 1.0)
    shadow = _shade(color, 0.82)
    draw.rectangle([0.26 * w, 0.30 * h, 0.74 * w, 0.84 * h], fill=body)
    draw.arc([0.32 * w, 0.12 * h, 0.50 * w, 0.36 * h], 180, 360, fill=shadow, width=14)
    draw.arc([0.50 * w, 0.12 * h, 0.68 * w, 0.36 * h], 180, 360, fill=shadow, width=14)
    draw.rectangle([0.26 * w, 0.30 * h, 0.74 * w, 0.33 * h], fill=shadow)


def _draw_garment(image: Image.Image, template: GarmentTemplate, color: str) -> None:
    draw = ImageDraw.Draw(image)
    if template.key.startswith("cap"):
        _draw_cap(draw, color)
    elif template.key.startswith("beanie"):
        _draw_beanie(draw, color)
    elif template.key.startswith("jacket"):
        _draw_jacket(draw, color)
    elif template.key.startswith("tote"):
        _draw_tote(draw, color)
    else:
        _draw_garment_shirt(draw, template, color)


def _draw_garment_shirt(draw: ImageDraw.ImageDraw, template: GarmentTemplate, color: str) -> None:
    _draw_tshirt(draw, color, hoodie=template.key.startswith("hoodie"))
    if template.key.startswith("polo"):
        w = h = CANVAS
        draw.polygon(
            [(0.47 * w, 0.21 * h), (0.50 * w, 0.31 * h), (0.53 * w, 0.21 * h)],
            fill=_shade(color, 0.7),
        )


def _emboss(design: Image.Image) -> Image.Image:
    """Give the stitched patch a raised edge so it sits *on* the garment."""
    alpha = design.getchannel("A")
    shadow = Image.new("RGBA", design.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(6)))
    shadow = Image.composite(
        Image.new("RGBA", design.size, (0, 0, 0, 110)), shadow, alpha.point(lambda a: 255 - a)
    )
    out = Image.new("RGBA", design.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (3, 5))
    out.alpha_composite(design)
    return out


def render_mockup(
    pattern: EmbroideryPattern,
    template_key: str = "tshirt_left_chest",
    garment_color: str = "black",
    design_width_mm: float | None = None,
) -> bytes:
    """Composite a stitched design onto a garment template."""
    template = TEMPLATES.get(template_key)
    if template is None:
        raise ValueError(
            f"Unknown mockup template '{template_key}'. "
            f"Available: {', '.join(TEMPLATES)}"
        )

    color_hex = FABRIC_COLORS.get(garment_color, garment_color)
    if not color_hex.startswith("#"):
        raise ValueError(f"Unknown garment colour '{garment_color}'.")

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (245, 245, 247, 255))
    # Soft studio backdrop.
    backdrop = ImageDraw.Draw(canvas)
    backdrop.ellipse(
        [-0.2 * CANVAS, 0.55 * CANVAS, 1.2 * CANVAS, 1.35 * CANVAS], fill=(232, 232, 236, 255)
    )
    _draw_garment(canvas, template, color_hex)

    # Render the stitches on transparent ground.
    design_png = render_png(
        pattern,
        RenderOptions(width=1000, height=1000, padding=0, background="white",
                      fabric_texture=False, shadow=True),
    )
    design = Image.open(io.BytesIO(design_png)).convert("RGBA")

    # Drop the flat white ground the renderer painted, keeping the thread.
    pixels = design.load()
    for y in range(design.height):
        for x in range(design.width):
            r, g, b, a = pixels[x, y]
            if abs(r - 245) < 8 and abs(g - 245) < 8 and abs(b - 243) < 8:
                pixels[x, y] = (r, g, b, 0)

    bbox = design.getbbox()
    if bbox:
        design = design.crop(bbox)

    # Scale to the true physical size inside the placement window.
    px, py, pw, ph = template.placement
    window_w = pw * CANVAS
    window_h = ph * CANVAS

    pattern_w_mm, pattern_h_mm = pattern.size_mm()
    target_mm = design_width_mm or pattern_w_mm or template.placement_width_mm
    px_per_mm = window_w / template.placement_width_mm
    target_px_w = max(24.0, target_mm * px_per_mm)
    scale = target_px_w / max(1, design.width)
    target_px_h = design.height * scale

    if target_px_h > window_h:
        scale *= window_h / target_px_h
        target_px_w = design.width * scale
        target_px_h = design.height * scale

    design = design.resize(
        (max(1, int(target_px_w)), max(1, int(target_px_h))), Image.LANCZOS
    )
    design = _emboss(design)

    offset = (
        int(px * CANVAS + (window_w - design.width) / 2),
        int(py * CANVAS + (window_h - design.height) / 2),
    )
    canvas.alpha_composite(design, offset)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def list_templates() -> list[dict]:
    return [
        {
            "key": t.key,
            "name": t.name,
            "fabric_profile": t.fabric_profile,
            "placement": t.placement_name,
            "placement_width_mm": t.placement_width_mm,
            "default_color": t.default_color,
        }
        for t in TEMPLATES.values()
    ]


def list_colors() -> list[dict]:
    return [{"key": key, "hex": value, "name": key.replace("_", " ").title()}
            for key, value in FABRIC_COLORS.items()]
