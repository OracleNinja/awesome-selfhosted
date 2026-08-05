"""Design pipeline orchestration.

Ties the embroidery engine to storage and the database:

    upload -> analyze -> vectorize -> digitize -> preview -> export

Each stage persists its artefacts so a design can be resumed, re-digitized
with different settings, or re-exported without re-uploading anything.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.ai import AIContext
from app.core.config import settings
from app.embroidery import (
    DigitizeOptions,
    RenderOptions,
    TextOptions,
    TraceOptions,
    build_package,
    digitize_shapes,
    digitize_svg,
    estimate_runtime_minutes,
    get_fabric,
    render_png,
    render_svg,
    stitch_stream,
    text_to_shapes,
    trace_image,
)
from app.embroidery.optimizer import StitchBudgetExceeded
from app.embroidery.pattern import EmbroideryPattern
from app.embroidery.raster import thumbnail
from app.embroidery.svg_parse import parse_svg
from app.models import (
    Design,
    DesignFile,
    DesignSource,
    DesignStatus,
    EmbroiderySettings,
    FileKind,
    Machine,
)
from app.services import plans
from app.services.analyzer import analyze_design
from app.services.storage import build_path, checksum, get_storage

log = logging.getLogger(__name__)

RASTER_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp",
                "image/heic", "image/heif", "image/tiff"}
VECTOR_TYPES = {"image/svg+xml", "text/xml", "application/xml"}


class PipelineError(Exception):
    """A user-fixable problem with the artwork or the request."""


# ------------------------------------------------------------------- helpers
def _register_heif() -> None:
    """HEIC comes off iPhones constantly; register the decoder if present."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # pragma: no cover - optional dependency
        pass


_register_heif()


def store_file(
    db: Session,
    design: Design,
    kind: FileKind,
    filename: str,
    data: bytes,
    content_type: str,
    meta: dict | None = None,
) -> DesignFile:
    storage = get_storage()
    path = build_path(str(design.organization_id), str(design.id), kind.value, filename)
    storage.put(path, data, content_type)

    existing = next(
        (f for f in design.files if f.kind == kind and f.filename == filename), None
    )
    if existing:
        existing.storage_path = path
        existing.size_bytes = len(data)
        existing.checksum = checksum(data)
        existing.content_type = content_type
        existing.meta = meta or {}
        return existing

    record = DesignFile(
        design_id=design.id,
        kind=kind,
        filename=filename,
        storage_path=path,
        content_type=content_type,
        size_bytes=len(data),
        checksum=checksum(data),
        meta=meta or {},
    )
    db.add(record)
    design.files.append(record)
    return record


def file_of(design: Design, kind: FileKind) -> DesignFile | None:
    matches = [f for f in design.files if f.kind == kind]
    return matches[-1] if matches else None


def load_file(design: Design, kind: FileKind) -> bytes:
    record = file_of(design, kind)
    if record is None:
        raise PipelineError(f"This design has no {kind.value} file yet.")
    return get_storage().get(record.storage_path)


def guess_kind(content_type: str, filename: str) -> str:
    lowered = (filename or "").lower()
    if content_type in VECTOR_TYPES or lowered.endswith(".svg"):
        return "vector"
    if content_type in RASTER_TYPES or lowered.rsplit(".", 1)[-1] in (
        "png", "jpg", "jpeg", "webp", "bmp", "heic", "heif", "tif", "tiff"
    ):
        return "raster"
    raise PipelineError(
        f"Unsupported file type '{content_type or filename}'. Upload a PNG, JPG, "
        "HEIC, BMP, WEBP or SVG."
    )


# --------------------------------------------------------------------- ingest
def create_design_from_upload(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    data: bytes,
    filename: str,
    content_type: str,
    customer_id: uuid.UUID | None = None,
    run_analysis: bool = True,
    use_ai: bool = True,
) -> Design:
    if len(data) > settings.max_upload_bytes:
        raise PipelineError(
            f"File is {len(data) / 1_048_576:.1f} MB; the limit is {settings.max_upload_mb} MB."
        )

    kind = guess_kind(content_type, filename)
    source = DesignSource.upload_svg if kind == "vector" else DesignSource.upload_image

    design = Design(
        organization_id=organization_id,
        owner_id=user_id,
        customer_id=customer_id,
        name=name or filename or "Untitled design",
        source=source,
        status=DesignStatus.analyzing if run_analysis else DesignStatus.draft,
    )
    db.add(design)
    db.flush()

    store_file(db, design, FileKind.original, filename or "artwork", data, content_type)

    if kind == "raster":
        try:
            store_file(
                db, design, FileKind.thumbnail, "thumbnail.png",
                thumbnail(data), "image/png",
            )
        except Exception as exc:
            log.warning("Thumbnail generation failed for %s: %s", design.id, exc)

    if run_analysis:
        try:
            if kind == "raster":
                report = analyze_design(
                    data,
                    content_type,
                    use_ai=use_ai,
                    ai_context=AIContext(
                        db=db, organization_id=organization_id, user_id=user_id
                    ),
                )
            else:
                report = _analyze_vector(data)
            design.analysis = report.to_dict()
            design.compatibility_score = report.compatibility_score
            design.status = DesignStatus.draft
        except Exception as exc:
            log.exception("Analysis failed for design %s", design.id)
            design.status = DesignStatus.draft
            design.error_message = f"Analysis could not complete: {exc}"

    plans.record_usage(db, organization_id, user_id, "design", design_id=str(design.id))
    return design


def _analyze_vector(data: bytes):
    """Vector artwork skips the CV metrology — the geometry is already exact."""
    from app.services.analyzer import AnalysisReport

    document = parse_svg(data)
    colors: dict[str, int] = {}
    for shape in document.shapes:
        for color in (shape.fill, shape.stroke):
            if color:
                key = f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"
                colors[key] = colors.get(key, 0) + 1
    total = sum(colors.values()) or 1

    issues = []
    recommendations = ["Vector artwork — edges will trace exactly, no tracing loss."]
    score = 96
    if len(colors) > 12:
        score -= 20
        issues.append({
            "level": "warning", "code": "too_many_colors",
            "message": f"{len(colors)} colours in the file. Reduce to 6 or fewer to keep "
                       "colour changes manageable.",
        })
    if not document.shapes:
        score = 10
        issues.append({
            "level": "error", "code": "empty_svg",
            "message": "No stitchable shapes found. If the file contains live text, "
                       "convert it to outlines and re-upload.",
        })

    from app.services.analyzer import verdict_for

    return AnalysisReport(
        compatibility_score=score,
        verdict=verdict_for(score),
        detected={
            "colors": len(colors),
            "shapes": len(document.shapes),
            "artwork_type": "vector",
            "text": None,
        },
        colors=[
            {"hex": hexv, "share": round(count / total, 3), "rgb": [
                int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)]}
            for hexv, count in sorted(colors.items(), key=lambda kv: -kv[1])
        ],
        recommendations=recommendations,
        issues=issues,
        metrics={"shape_count": len(document.shapes)},
        suggested_colors=min(len(colors), 6) or 3,
    )


def create_design_from_text(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    options: TextOptions,
    customer_id: uuid.UUID | None = None,
) -> tuple[Design, list]:
    design = Design(
        organization_id=organization_id,
        owner_id=user_id,
        customer_id=customer_id,
        name=name or options.text[:60] or "Lettering",
        source=DesignSource.text,
        status=DesignStatus.vectorized,
        analysis={
            "compatibility_score": 98,
            "verdict": "Lettering renders as satin columns — excellent for embroidery.",
            "detected": {"text": True, "text_content": options.text, "artwork_type": "lettering"},
            "recommendations": [
                f"Keep cap height at or above the fabric minimum "
                f"({options.height_mm:.0f} mm requested)."
            ],
            "issues": [],
        },
        compatibility_score=98,
    )
    db.add(design)
    db.flush()

    shapes = text_to_shapes(options)
    svg = _shapes_to_svg(shapes)
    store_file(db, design, FileKind.vector, "lettering.svg", svg.encode(), "image/svg+xml")

    plans.record_usage(db, organization_id, user_id, "design", design_id=str(design.id))
    return design, shapes


def _shapes_to_svg(shapes: list) -> str:
    min_x = min((p[0] for s in shapes for r in s.rings for p in r), default=0.0)
    min_y = min((p[1] for s in shapes for r in s.rings for p in r), default=0.0)
    max_x = max((p[0] for s in shapes for r in s.rings for p in r), default=1.0)
    max_y = max((p[1] for s in shapes for r in s.rings for p in r), default=1.0)
    width, height = max(1.0, max_x - min_x), max(1.0, max_y - min_y)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.1f} {min_y:.1f} '
        f'{width:.1f} {height:.1f}" width="{width / 10:.2f}mm" height="{height / 10:.2f}mm">'
    ]
    for shape in shapes:
        if shape.fill is None:
            continue
        d = " ".join(
            "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in ring) + " Z"
            for ring in shape.rings if len(ring) >= 3
        )
        if not d:
            continue
        r, g, b = shape.fill
        parts.append(f'<path d="{d}" fill="#{r:02X}{g:02X}{b:02X}" fill-rule="evenodd"/>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ vectorize
def vectorize_design(db: Session, design: Design, options: TraceOptions) -> dict:
    """Trace the original raster into an editable vector layer set."""
    original = file_of(design, FileKind.original)
    if original is None:
        raise PipelineError("This design has no source artwork.")

    data = get_storage().get(original.storage_path)
    if guess_kind(original.content_type, original.filename) == "vector":
        raise PipelineError("This design is already vector artwork.")

    result = trace_image(data, options)
    svg = result.to_svg()
    store_file(db, design, FileKind.vector, "vector.svg", svg.encode(), "image/svg+xml",
               meta={"layers": len(result.shapes), "background_removed": result.background_removed})

    design.status = DesignStatus.vectorized
    return {
        "layers": len(result.shapes),
        "palette": [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in result.palette],
        "coverage": result.coverage,
        "background_removed": result.background_removed,
        "width_mm": round(result.width_px * result.scale_units_per_px / 10, 1),
        "height_mm": round(result.height_px * result.scale_units_per_px / 10, 1),
        "svg": svg if len(svg) < 400_000 else None,
    }


# ------------------------------------------------------------------- digitize
def _machine_limits(db: Session, machine_id: uuid.UUID | None) -> tuple[Machine | None, dict]:
    if machine_id is None:
        return (None, {})
    machine = db.get(Machine, machine_id)
    if machine is None:
        return (None, {})
    return (
        machine,
        {
            "hoop_width_mm": machine.hoop_width_mm,
            "hoop_height_mm": machine.hoop_height_mm,
            "max_stitches": machine.max_stitch_count,
            "needle_count": machine.needle_count,
        },
    )


def digitize_design(
    db: Session,
    design: Design,
    *,
    fabric: str = "cotton_shirt",
    thread_catalog: str = "genie_standard",
    machine_id: uuid.UUID | None = None,
    target_width_mm: float | None = None,
    target_height_mm: float | None = None,
    fill_angle_deg: float = 45.0,
    density_scale: float = 1.0,
    auto_underlay: bool = True,
    auto_satin: bool = True,
    max_colors: int | None = None,
    trace_options: TraceOptions | None = None,
) -> dict:
    """Run the digitizing engine and persist every artefact."""
    design.status = DesignStatus.digitizing
    machine, limits = _machine_limits(db, machine_id)

    options = DigitizeOptions(
        fabric=fabric,
        thread_catalog=thread_catalog,
        target_width_mm=target_width_mm,
        target_height_mm=target_height_mm,
        fill_angle_deg=fill_angle_deg,
        density_scale=density_scale,
        auto_underlay=auto_underlay,
        auto_satin=auto_satin,
        max_colors=max_colors,
        **limits,
    )

    try:
        vector = file_of(design, FileKind.vector)
        if vector is not None:
            svg_text = get_storage().get(vector.storage_path).decode("utf-8", "replace")
            result = digitize_svg(svg_text, options)
        else:
            original = file_of(design, FileKind.original)
            if original is None:
                raise PipelineError("This design has no artwork to digitize.")
            data = get_storage().get(original.storage_path)
            if guess_kind(original.content_type, original.filename) == "vector":
                result = digitize_svg(data.decode("utf-8", "replace"), options)
            else:
                trace = trace_image(
                    data,
                    trace_options
                    or TraceOptions(
                        max_colors=max_colors or 6,
                        target_width_mm=target_width_mm,
                    ),
                )
                store_file(db, design, FileKind.vector, "vector.svg",
                           trace.to_svg().encode(), "image/svg+xml")
                result = digitize_shapes(trace.shapes, options)
    except PipelineError:
        design.status = DesignStatus.failed
        raise
    except StitchBudgetExceeded as exc:
        design.status = DesignStatus.failed
        design.error_message = str(exc)
        raise PipelineError(str(exc)) from exc
    except Exception as exc:
        design.status = DesignStatus.failed
        design.error_message = str(exc)
        log.exception("Digitizing failed for design %s", design.id)
        raise PipelineError(f"Digitizing failed: {exc}") from exc

    pattern = result.pattern
    summary = pattern.summary()
    fabric_profile = get_fabric(fabric)
    runtime = estimate_runtime_minutes(
        pattern.stitch_count,
        max(0, pattern.color_count - 1),
        pattern.trim_count,
        speed_spm=machine.max_speed_spm if machine else 800,
        speed_factor=fabric_profile.speed_factor,
    )

    # Persist artefacts.
    preview = render_png(pattern, RenderOptions(width=1200, height=1200))
    store_file(db, design, FileKind.preview, "preview.png", preview, "image/png")
    store_file(db, design, FileKind.preview, "preview.svg",
               render_svg(pattern).encode(), "image/svg+xml")

    design.stitch_count = summary["stitch_count"]
    design.color_count = summary["color_count"]
    design.width_mm = summary["width_mm"]
    design.height_mm = summary["height_mm"]
    design.trim_count = summary["trim_count"]
    design.thread_length_m = summary["thread_length_m"]
    design.estimated_minutes = runtime
    design.thread_chart = summary["colors"]
    design.issues = result.issues
    design.stitch_preview = stitch_stream(pattern, settings.max_stitch_preview_points)
    design.status = DesignStatus.ready
    design.error_message = None

    _save_settings(
        db, design,
        fabric=fabric, thread_catalog=thread_catalog, machine_id=machine_id,
        target_width_mm=target_width_mm, target_height_mm=target_height_mm,
        fill_angle_deg=fill_angle_deg, density_scale=density_scale,
        auto_underlay=auto_underlay, auto_satin=auto_satin, max_colors=max_colors,
    )

    return {
        **summary,
        "issues": result.issues,
        "objects": result.objects[:120],
        "estimated_minutes": runtime,
        "fabric": fabric_profile.to_dict(),
        "machine": {
            "id": str(machine.id), "name": machine.name, "brand": machine.brand,
            "model": machine.model, "needle_count": machine.needle_count,
            "max_speed_spm": machine.max_speed_spm,
            "hoop": f"{machine.hoop_width_mm:.0f} × {machine.hoop_height_mm:.0f} mm",
        } if machine else None,
    }


def _save_settings(db: Session, design: Design, **values) -> None:
    record = design.settings
    if record is None:
        record = EmbroiderySettings(design_id=design.id)
        db.add(record)
        design.settings = record
    for key, value in values.items():
        if hasattr(record, key):
            setattr(record, key, value)


def rebuild_pattern(db: Session, design: Design) -> EmbroideryPattern:
    """Re-run the engine with the design's stored settings.

    Stitch data is regenerated rather than stored as a blob: settings plus a
    versioned engine reproduce it exactly, and a design stays re-renderable
    after an engine improvement.
    """
    stored = design.settings
    machine_id = stored.machine_id if stored else None
    machine, limits = _machine_limits(db, machine_id)

    options = DigitizeOptions(
        fabric=stored.fabric if stored else "cotton_shirt",
        thread_catalog=stored.thread_catalog if stored else "genie_standard",
        target_width_mm=stored.target_width_mm if stored else None,
        target_height_mm=stored.target_height_mm if stored else None,
        fill_angle_deg=stored.fill_angle_deg if stored else 45.0,
        density_scale=stored.density_scale if stored else 1.0,
        auto_underlay=stored.auto_underlay if stored else True,
        auto_satin=stored.auto_satin if stored else True,
        max_colors=stored.max_colors if stored else None,
        **limits,
    )

    vector = file_of(design, FileKind.vector)
    if vector is not None:
        svg_text = get_storage().get(vector.storage_path).decode("utf-8", "replace")
        return digitize_svg(svg_text, options).pattern

    original = file_of(design, FileKind.original)
    if original is None:
        raise PipelineError("This design has no artwork.")
    data = get_storage().get(original.storage_path)
    if guess_kind(original.content_type, original.filename) == "vector":
        return digitize_svg(data.decode("utf-8", "replace"), options).pattern
    trace = trace_image(data, TraceOptions(max_colors=options.max_colors or 6))
    return digitize_shapes(trace.shapes, options).pattern


# --------------------------------------------------------------------- export
def export_design(
    db: Session,
    design: Design,
    formats: list[str],
    machine: Machine | None = None,
    order: dict | None = None,
    preview_background: str = "white",
) -> tuple[bytes, dict]:
    if design.status != DesignStatus.ready:
        raise PipelineError(
            "Digitize the design before exporting — there are no stitches yet."
        )

    pattern = rebuild_pattern(db, design)
    fabric_key = design.settings.fabric if design.settings else "cotton_shirt"

    machine_info = None
    if machine:
        machine_info = {
            "brand": machine.brand, "model": machine.model,
            "needle_count": machine.needle_count,
            "max_speed_spm": machine.max_speed_spm,
            "hoop": f"{machine.hoop_width_mm:.0f} × {machine.hoop_height_mm:.0f} mm",
        }

    data, manifest = build_package(
        pattern,
        design_name=design.name,
        formats=formats,
        fabric_key=fabric_key,
        machine=machine_info,
        issues=design.issues,
        order=order,
        preview_background=preview_background,
    )
    store_file(
        db, design, FileKind.package,
        f"{design.name[:40].strip().replace(' ', '_') or 'design'}_package.zip",
        data, "application/zip", meta=manifest,
    )
    return data, manifest


def summarize(design: Design, include_stream: bool = False) -> dict:
    """Serialise a design for the API."""
    storage = get_storage()
    files = []
    for record in design.files:
        files.append({
            "id": str(record.id),
            "kind": record.kind.value,
            "filename": record.filename,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "url": storage.url(record.storage_path),
        })

    payload = {
        "id": str(design.id),
        "name": design.name,
        "description": design.description,
        "status": design.status.value,
        "source": design.source.value,
        "tags": design.tags or [],
        "customer_id": str(design.customer_id) if design.customer_id else None,
        "compatibility_score": design.compatibility_score,
        "analysis": design.analysis or {},
        "stitch_count": design.stitch_count,
        "color_count": design.color_count,
        "width_mm": design.width_mm,
        "height_mm": design.height_mm,
        "trim_count": design.trim_count,
        "thread_length_m": design.thread_length_m,
        "estimated_minutes": design.estimated_minutes,
        "thread_chart": design.thread_chart or [],
        "issues": design.issues or [],
        "error_message": design.error_message,
        "files": files,
        "created_at": design.created_at.isoformat() if design.created_at else None,
        "updated_at": design.updated_at.isoformat() if design.updated_at else None,
        "settings": (
            {
                k: (str(v) if isinstance(v, uuid.UUID) else v)
                for k, v in {
                    "fabric": design.settings.fabric,
                    "thread_catalog": design.settings.thread_catalog,
                    "machine_id": design.settings.machine_id,
                    "target_width_mm": design.settings.target_width_mm,
                    "target_height_mm": design.settings.target_height_mm,
                    "fill_angle_deg": design.settings.fill_angle_deg,
                    "density_scale": design.settings.density_scale,
                    "auto_underlay": design.settings.auto_underlay,
                    "auto_satin": design.settings.auto_satin,
                    "max_colors": design.settings.max_colors,
                }.items()
            }
            if design.settings
            else None
        ),
    }
    if include_stream:
        payload["stitch_preview"] = design.stitch_preview
    return payload
