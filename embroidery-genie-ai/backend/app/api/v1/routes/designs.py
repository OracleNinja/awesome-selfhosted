"""Design lifecycle: upload, analyze, vectorize, digitize, preview, export."""

from __future__ import annotations

import re
import unicodedata
import uuid
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.ai import AIContext
from app.core.config import settings
from app.core.deps import CurrentContext, DbSession
from app.core.ratelimit import rate_limit
from app.embroidery import TextOptions, TraceOptions
from app.models import Design, DesignStatus, FileKind, Machine, Order
from app.schemas import (
    DesignCreate,
    DesignUpdate,
    DigitizeRequest,
    ExportRequest,
    Message,
    MockupRequest,
    TextDesignRequest,
    VectorizeRequest,
)
from app.services import designs as pipeline
from app.services import plans
from app.services.analyzer import analyze_design
from app.embroidery.package import safe_name
from app.services.mockup import render_mockup
from app.services.storage import get_storage

router = APIRouter(prefix="/designs", tags=["designs"])


def _get(db: DbSession, context: CurrentContext, design_id: uuid.UUID) -> Design:
    design = db.scalar(
        select(Design)
        .options(selectinload(Design.files), selectinload(Design.settings))
        .where(Design.id == design_id, Design.organization_id == context.org_id)
    )
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")
    return design


UPLOAD_CHUNK = 1024 * 1024


async def _read_limited(file: UploadFile, request: Request) -> bytes:
    """Read an upload without letting it size the process.

    ``await file.read()`` with no argument buffers the entire body first, so a
    multi-gigabyte POST exhausts memory before any size check can run. Reject
    on the declared Content-Length when we have one, then read in chunks and
    stop the moment the real body exceeds the limit.
    """
    limit = settings.max_upload_bytes

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status_code=413,  # Content Too Large
            detail=f"Upload is {int(declared) / 1_048_576:.1f} MB; the limit is "
                   f"{settings.max_upload_mb} MB.",
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,  # Content Too Large
                detail=f"Upload exceeds the {settings.max_upload_mb} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _quota_guard(db: DbSession, context: CurrentContext) -> None:
    try:
        plans.assert_can_create_design(db, context.org_id)
    except plans.QuotaExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)
        ) from exc


# ----------------------------------------------------------------- listing
@router.get("")
def list_designs(
    context: CurrentContext,
    db: DbSession,
    q: str | None = Query(None, max_length=120),
    status_filter: str | None = Query(None, alias="status"),
    customer_id: uuid.UUID | None = None,
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    stmt = select(Design).where(Design.organization_id == context.org_id)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Design.name.ilike(pattern), Design.description.ilike(pattern)))
    if status_filter:
        try:
            stmt = stmt.where(Design.status == DesignStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Unknown status '{status_filter}'.") from exc
    if customer_id:
        stmt = stmt.where(Design.customer_id == customer_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(selectinload(Design.files), selectinload(Design.settings))
        .order_by(Design.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [pipeline.summarize(d) for d in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{design_id}")
def get_design(design_id: uuid.UUID, context: CurrentContext, db: DbSession) -> dict:
    return pipeline.summarize(_get(db, context, design_id), include_stream=False)


@router.get("/{design_id}/stitches")
def get_stitches(design_id: uuid.UUID, context: CurrentContext, db: DbSession) -> dict:
    """Stitch stream for the 3D simulator."""
    design = _get(db, context, design_id)
    if not design.stitch_preview:
        raise HTTPException(
            status_code=409,
            detail="This design has not been digitized yet, so it has no stitches to show.",
        )
    return design.stitch_preview


@router.patch("/{design_id}")
def update_design(
    design_id: uuid.UUID, payload: DesignUpdate, context: CurrentContext, db: DbSession
) -> dict:
    design = _get(db, context, design_id)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"]:
        try:
            design.status = DesignStatus(data.pop("status"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown status.") from exc
    for field, value in data.items():
        setattr(design, field, value)
    db.flush()
    return pipeline.summarize(design)


@router.delete("/{design_id}", response_model=Message)
def delete_design(design_id: uuid.UUID, context: CurrentContext, db: DbSession) -> Message:
    design = _get(db, context, design_id)
    get_storage().delete_prefix(f"{context.org_id}/{design.id}")
    db.delete(design)
    return Message(detail="Design deleted.")


# ------------------------------------------------------------------ creation
@router.post("/upload", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("upload"))])
async def upload_design(
    request: Request,
    context: CurrentContext,
    db: DbSession,
    file: UploadFile = File(...),
    name: str = Form(""),
    customer_id: str = Form(""),
    analyze: bool = Form(True),
    use_ai: bool = Form(True),
) -> dict:
    """Upload artwork (PNG/JPG/HEIC/BMP/WEBP/SVG) and run the analyzer."""
    _quota_guard(db, context)
    data = await _read_limited(file, request)
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")

    try:
        design = pipeline.create_design_from_upload(
            db,
            organization_id=context.org_id,
            user_id=context.user_id,
            name=name or (file.filename or "Untitled design"),
            data=data,
            filename=file.filename or "artwork",
            content_type=file.content_type or "application/octet-stream",
            customer_id=uuid.UUID(customer_id) if customer_id else None,
            run_analysis=analyze,
            use_ai=use_ai,
        )
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.flush()
    return pipeline.summarize(design)


@router.post("/text", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("digitize"))])
def create_text_design(
    payload: TextDesignRequest, context: CurrentContext, db: DbSession
) -> dict:
    """Create a lettering design from text."""
    _quota_guard(db, context)
    color = payload.color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    rgb = (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))

    try:
        design, _shapes = pipeline.create_design_from_text(
            db,
            organization_id=context.org_id,
            user_id=context.user_id,
            name=payload.name or payload.text[:60],
            customer_id=payload.customer_id,
            options=TextOptions(
                text=payload.text,
                font_key=payload.font_key,
                height_mm=payload.height_mm,
                letter_spacing=payload.letter_spacing,
                line_spacing=payload.line_spacing,
                color=rgb,
                align=payload.align,
                arc_degrees=payload.arc_degrees,
            ),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, pipeline.PipelineError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.flush()
    return pipeline.summarize(design)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_blank(payload: DesignCreate, context: CurrentContext, db: DbSession) -> dict:
    """Create an empty design record (artwork uploaded separately)."""
    _quota_guard(db, context)
    design = Design(
        organization_id=context.org_id,
        owner_id=context.user_id,
        customer_id=payload.customer_id,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
    )
    db.add(design)
    plans.record_usage(db, context.org_id, context.user_id, "design")
    db.flush()
    return pipeline.summarize(design)


# ------------------------------------------------------------------ pipeline
@router.post("/{design_id}/analyze", dependencies=[Depends(rate_limit("ai_analysis"))])
def analyze(
    design_id: uuid.UUID,
    context: CurrentContext,
    db: DbSession,
    use_ai: bool = Query(True),
) -> dict:
    """Re-run the AI design analyzer."""
    design = _get(db, context, design_id)
    original = pipeline.file_of(design, FileKind.original)
    if original is None:
        raise HTTPException(status_code=409, detail="This design has no source artwork.")

    data = get_storage().get(original.storage_path)
    if pipeline.guess_kind(original.content_type, original.filename) == "vector":
        report = pipeline._analyze_vector(data)
    else:
        report = analyze_design(
            data,
            original.content_type,
            use_ai=use_ai,
            ai_context=AIContext(
                db=db, organization_id=context.org_id, user_id=context.user_id
            ),
        )

    design.analysis = report.to_dict()
    design.compatibility_score = report.compatibility_score
    plans.record_usage(db, context.org_id, context.user_id, "ai_analysis")
    db.flush()
    return design.analysis


@router.post("/{design_id}/vectorize", dependencies=[Depends(rate_limit("digitize"))])
def vectorize(
    design_id: uuid.UUID, payload: VectorizeRequest, context: CurrentContext, db: DbSession
) -> dict:
    design = _get(db, context, design_id)
    try:
        result = pipeline.vectorize_design(
            db, design,
            TraceOptions(
                max_colors=payload.max_colors,
                remove_background=payload.remove_background,
                background_tolerance=payload.background_tolerance,
                smooth=payload.smooth,
                despeckle=payload.despeckle,
                target_width_mm=payload.target_width_mm,
            ),
        )
    except (pipeline.PipelineError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.flush()
    return result


@router.post("/{design_id}/digitize", dependencies=[Depends(rate_limit("digitize"))])
def digitize(
    design_id: uuid.UUID, payload: DigitizeRequest, context: CurrentContext, db: DbSession
) -> dict:
    """Convert the artwork into stitches."""
    design = _get(db, context, design_id)
    if payload.machine_id:
        machine = db.get(Machine, payload.machine_id)
        if machine is None or machine.organization_id != context.org_id:
            raise HTTPException(status_code=404, detail="Machine not found.")
    try:
        result = pipeline.digitize_design(
            db, design,
            fabric=payload.fabric,
            thread_catalog=payload.thread_catalog,
            machine_id=payload.machine_id,
            target_width_mm=payload.target_width_mm,
            target_height_mm=payload.target_height_mm,
            fill_angle_deg=payload.fill_angle_deg,
            density_scale=payload.density_scale,
            auto_underlay=payload.auto_underlay,
            auto_satin=payload.auto_satin,
            max_colors=payload.max_colors,
        )
    except (pipeline.PipelineError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.flush()
    return result


@router.post("/{design_id}/export", dependencies=[Depends(rate_limit("export"))])
def export(
    design_id: uuid.UUID, payload: ExportRequest, context: CurrentContext, db: DbSession
) -> Response:
    """Build the production ZIP: stitch files, thread chart, run sheet, preview."""
    design = _get(db, context, design_id)

    for extension in payload.formats:
        try:
            plans.assert_format_allowed(db, context.org_id, extension)
        except plans.FeatureLocked as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)
            ) from exc

    machine = None
    if payload.machine_id:
        machine = db.get(Machine, payload.machine_id)
        if machine is None or machine.organization_id != context.org_id:
            raise HTTPException(status_code=404, detail="Machine not found.")

    order_info = None
    if payload.order_id:
        order = db.get(Order, payload.order_id)
        if order is not None and order.organization_id == context.org_id:
            order_info = {
                "number": order.number,
                "customer": order.customer.name if order.customer else "—",
                "quantity": sum(i.quantity for i in order.items),
                "placement": order.placement,
                "due_date": order.due_date.isoformat() if order.due_date else "—",
            }

    try:
        data, manifest = pipeline.export_design(
            db, design, payload.formats, machine, order_info, payload.preview_background
        )
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    plans.record_usage(
        db, context.org_id, context.user_id, "export",
        design_id=str(design.id), formats=payload.formats,
    )
    db.flush()

    return Response(
        content=data,
        media_type="application/zip",
        headers=_download_headers(
            f"{safe_name(design.name)}_package.zip", manifest.get("warnings", [])
        ),
    )


def _download_headers(filename: str, warnings: list[str]) -> dict[str, str]:
    """Build download headers that survive non-ASCII design names.

    HTTP headers are latin-1; real design names routinely contain em dashes,
    accents and CJK. RFC 5987 gives the UTF-8 name, with an ASCII fallback for
    clients that ignore it.
    """
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', "_", ascii_name).strip("._") or "design_package.zip"
    quoted = quote(filename, safe="")
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}',
    }
    if warnings:
        text = "; ".join(warnings)[:400]
        headers["X-Export-Warnings"] = text.encode("ascii", "replace").decode()
    return headers


@router.post("/{design_id}/mockup", dependencies=[Depends(rate_limit("mockup"))])
def mockup(
    design_id: uuid.UUID, payload: MockupRequest, context: CurrentContext, db: DbSession
) -> Response:
    """Render the design onto a garment for customer approval."""
    design = _get(db, context, design_id)
    if design.status != DesignStatus.ready:
        raise HTTPException(
            status_code=409, detail="Digitize the design before generating a mockup."
        )
    try:
        pattern = pipeline.rebuild_pattern(db, design)
        image = render_mockup(
            pattern, payload.template, payload.garment_color, payload.design_width_mm
        )
    except (ValueError, pipeline.PipelineError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pipeline.store_file(
        db, design, FileKind.mockup,
        f"mockup_{payload.template}_{payload.garment_color}.png", image, "image/png",
    )
    db.flush()
    return Response(content=image, media_type="image/png")


@router.post("/{design_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate(design_id: uuid.UUID, context: CurrentContext, db: DbSession) -> dict:
    """Copy a design so a variant can be digitized without losing the original."""
    _quota_guard(db, context)
    source = _get(db, context, design_id)

    copy = Design(
        organization_id=context.org_id,
        owner_id=context.user_id,
        customer_id=source.customer_id,
        name=f"{source.name} (copy)",
        description=source.description,
        source=source.source,
        tags=list(source.tags or []),
        analysis=source.analysis,
        compatibility_score=source.compatibility_score,
        status=DesignStatus.draft,
    )
    db.add(copy)
    db.flush()

    storage = get_storage()
    for record in source.files:
        if record.kind in (FileKind.original, FileKind.vector, FileKind.thumbnail):
            try:
                pipeline.store_file(
                    db, copy, record.kind, record.filename,
                    storage.get(record.storage_path), record.content_type,
                )
            except FileNotFoundError:
                continue

    plans.record_usage(db, context.org_id, context.user_id, "design", design_id=str(copy.id))
    db.flush()
    return pipeline.summarize(copy)
