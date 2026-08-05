"""Embroidery Genie AI — digitizing engine.

Format-neutral, dependency-light core that turns vector or raster artwork into
machine-ready stitch files.  Nothing in this package imports the web layer, so
it can be used as a library, from a worker, or from a CLI.

    from app.embroidery import digitize_svg, DigitizeOptions, write

    result = digitize_svg(svg_text, DigitizeOptions(fabric="hat"))
    dst_bytes = write(result.pattern, "dst")
"""

from .digitizer import (
    DigitizeOptions,
    DigitizeResult,
    digitize_shapes,
    digitize_svg,
    estimate_runtime_minutes,
)
from .fabrics import FABRIC_PROFILES, FabricProfile, get_fabric, list_fabrics
from .package import build_package
from .pattern import Command, EmbroideryPattern, Stitch, StitchBlock, Thread
from .raster import TraceOptions, TraceResult, trace_image
from .render import RenderOptions, render_png, render_svg, stitch_stream
from .text import TextOptions, available_fonts, text_to_shapes
from .threads import GENIE_STANDARD, ThreadCatalog, get_catalog, suggest_thread
from .writers import available_formats, write

__all__ = [
    "Command",
    "DigitizeOptions",
    "DigitizeResult",
    "EmbroideryPattern",
    "FABRIC_PROFILES",
    "FabricProfile",
    "GENIE_STANDARD",
    "RenderOptions",
    "Stitch",
    "StitchBlock",
    "TextOptions",
    "Thread",
    "ThreadCatalog",
    "TraceOptions",
    "TraceResult",
    "available_fonts",
    "available_formats",
    "build_package",
    "digitize_shapes",
    "digitize_svg",
    "estimate_runtime_minutes",
    "get_catalog",
    "get_fabric",
    "list_fabrics",
    "render_png",
    "render_svg",
    "stitch_stream",
    "suggest_thread",
    "text_to_shapes",
    "trace_image",
    "write",
]
