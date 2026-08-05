"""Export package builder.

Produces the ZIP a shop hands to the floor:

    design.dst
    design.pes
    design.jef            (whatever formats were requested)
    thread_chart.pdf
    production_notes.pdf
    preview.png
    manifest.json
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone

from .digitizer import estimate_runtime_minutes
from .fabrics import get_fabric
from .pattern import EmbroideryPattern
from .render import RenderOptions, render_png, render_svg
from .reports import build_production_notes, build_thread_chart
from .writers import UnsupportedFormatError, write

DEFAULT_FORMATS = ("dst", "pes", "jef", "exp")


def safe_name(value: str, fallback: str = "design") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()).strip("._-")
    return cleaned[:60] or fallback


def build_package(
    pattern: EmbroideryPattern,
    design_name: str = "design",
    formats: list[str] | None = None,
    fabric_key: str = "cotton_shirt",
    machine: dict | None = None,
    issues: list[dict] | None = None,
    order: dict | None = None,
    preview_background: str = "white",
    include_svg: bool = True,
) -> tuple[bytes, dict]:
    """Return ``(zip_bytes, manifest)``."""
    formats = [f.lower().lstrip(".") for f in (formats or DEFAULT_FORMATS)]
    base = safe_name(design_name)
    fabric = get_fabric(fabric_key)

    preview = render_png(
        pattern, RenderOptions(width=1400, height=1400, background=preview_background)
    )

    speed = (machine or {}).get("max_speed_spm", 800)
    runtime = estimate_runtime_minutes(
        pattern.stitch_count,
        max(0, pattern.color_count - 1),
        pattern.trim_count,
        speed_spm=int(speed or 800),
        speed_factor=fabric.speed_factor,
    )

    manifest = {
        "product": "Embroidery Genie AI",
        "design": design_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fabric": fabric.to_dict(),
        "machine": machine,
        "summary": pattern.summary(),
        "estimated_runtime_minutes": runtime,
        "files": [],
        "warnings": [],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for extension in formats:
            try:
                data = write(pattern, extension)
            except UnsupportedFormatError as exc:
                manifest["warnings"].append(str(exc))
                continue
            except Exception as exc:  # pragma: no cover - writer backend failure
                manifest["warnings"].append(f"Could not write .{extension}: {exc}")
                continue
            filename = f"{base}.{extension}"
            archive.writestr(filename, data)
            manifest["files"].append({"name": filename, "bytes": len(data), "type": "stitch"})

        archive.writestr("preview.png", preview)
        manifest["files"].append({"name": "preview.png", "bytes": len(preview), "type": "preview"})

        if include_svg:
            svg = render_svg(pattern).encode("utf-8")
            archive.writestr("preview.svg", svg)
            manifest["files"].append({"name": "preview.svg", "bytes": len(svg), "type": "preview"})

        chart = build_thread_chart(pattern, design_name, preview)
        archive.writestr("thread_chart.pdf", chart)
        manifest["files"].append({"name": "thread_chart.pdf", "bytes": len(chart), "type": "doc"})

        notes = build_production_notes(
            pattern, design_name, fabric.to_dict(), machine, issues, runtime, order
        )
        archive.writestr("production_notes.pdf", notes)
        manifest["files"].append(
            {"name": "production_notes.pdf", "bytes": len(notes), "type": "doc"}
        )

        archive.writestr("manifest.json", json.dumps(manifest, indent=2))

    return buffer.getvalue(), manifest
