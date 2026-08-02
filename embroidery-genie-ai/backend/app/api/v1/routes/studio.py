"""Studio reference data and stateless tools.

Everything the Design Studio needs to render its option panels, plus two
stateless endpoints (`/preview` and `/estimate`) that let the UI show a real
result before a design is saved — which is what makes the studio feel
immediate rather than like a job queue.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.ratelimit import rate_limit

from app.embroidery import (
    DigitizeOptions,
    TextOptions,
    available_fonts,
    available_formats,
    digitize_shapes,
    estimate_runtime_minutes,
    get_catalog,
    list_fabrics,
    render_png,
    stitch_stream,
    text_to_shapes,
)
from app.embroidery.text import find_font, minimum_height_for_font
from app.embroidery.fabrics import get_fabric
from app.schemas import TextDesignRequest
from app.services.analyzer import PLACEMENTS
from app.services.machines import brands, list_presets
from app.services.mockup import list_colors, list_templates

router = APIRouter(prefix="/studio", tags=["studio"])


@router.get("/reference")
def reference() -> dict:
    """One call that primes the whole studio UI."""
    try:
        find_font()
        fonts_healthy = True
    except FileNotFoundError:
        fonts_healthy = False

    return {
        "fabrics": list_fabrics(),
        "formats": available_formats(),
        "fonts": available_fonts(),
        "fonts_healthy": fonts_healthy,
        "thread_catalogs": [get_catalog("genie_standard").to_dict()],
        "machine_presets": list_presets(),
        "machine_brands": brands(),
        "mockup_templates": list_templates(),
        "garment_colors": list_colors(),
        "placements": PLACEMENTS,
    }


@router.get("/fabrics")
def fabrics() -> dict:
    return {"fabrics": list_fabrics()}


@router.get("/formats")
def formats() -> dict:
    return {"formats": available_formats()}


@router.get("/threads/{catalog_key}")
def threads(catalog_key: str) -> dict:
    return get_catalog(catalog_key).to_dict()


@router.get("/fonts")
def fonts() -> dict:
    try:
        find_font()
        healthy = True
    except FileNotFoundError:
        healthy = False
    return {"fonts": available_fonts(), "healthy": healthy}


@router.post("/text/preview", dependencies=[Depends(rate_limit("preview"))])
def text_preview(payload: TextDesignRequest) -> dict:
    """Digitize lettering without saving anything — used by the live editor."""
    color = payload.color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    rgb = (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))

    try:
        shapes = text_to_shapes(
            TextOptions(
                text=payload.text,
                font_key=payload.font_key,
                height_mm=payload.height_mm,
                letter_spacing=payload.letter_spacing,
                line_spacing=payload.line_spacing,
                color=rgb,
                align=payload.align,
                arc_degrees=payload.arc_degrees,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = digitize_shapes(shapes, DigitizeOptions(fabric="cotton_shirt"))
    return {
        **result.pattern.summary(),
        "issues": result.issues,
        "stitches": stitch_stream(result.pattern, 40000),
        "minimum_height_mm": minimum_height_for_font(
            payload.font_key, get_fabric("cotton_shirt").min_letter_height_mm
        ),
    }


@router.post("/text/render.png", dependencies=[Depends(rate_limit("preview"))])
def text_render(payload: TextDesignRequest) -> Response:
    """PNG of the lettering as it will stitch."""
    color = payload.color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    rgb = (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
    try:
        shapes = text_to_shapes(
            TextOptions(
                text=payload.text, font_key=payload.font_key, height_mm=payload.height_mm,
                letter_spacing=payload.letter_spacing, line_spacing=payload.line_spacing,
                color=rgb, align=payload.align, arc_degrees=payload.arc_degrees,
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = digitize_shapes(shapes, DigitizeOptions())
    return Response(content=render_png(result.pattern), media_type="image/png")


@router.get("/estimate")
def estimate(
    stitch_count: int,
    color_count: int = 1,
    trims: int = 0,
    speed_spm: int = 800,
    fabric: str = "cotton_shirt",
) -> dict:
    """Run-time estimate without touching a design record."""
    profile = get_fabric(fabric)
    minutes = estimate_runtime_minutes(
        stitch_count, max(0, color_count - 1), trims,
        speed_spm=speed_spm, speed_factor=profile.speed_factor,
    )
    return {
        "minutes": minutes,
        "formatted": f"{int(minutes)}m {int((minutes % 1) * 60)}s",
        "effective_speed_spm": int(speed_spm * profile.speed_factor),
        "fabric": profile.name,
    }
