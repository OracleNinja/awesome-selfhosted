"""File serving for the local storage backend.

With Supabase Storage the client fetches signed URLs directly and never hits
this route.  With the local backend the API serves the bytes so tenancy is
still enforced — object storage without access control is how design files end
up on someone else's mood board.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentContext, DbSession
from app.models import Design, DesignFile
from app.services.storage import get_storage

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/raw/{path:path}")
def serve(path: str, context: CurrentContext, db: DbSession) -> Response:
    if settings.storage_backend != "local":
        raise HTTPException(
            status_code=404,
            detail="Direct file serving is only used with the local storage backend.",
        )

    # The path always begins with the owning organization id.
    if not path.startswith(f"{context.org_id}/"):
        raise HTTPException(status_code=403, detail="That file belongs to another workspace.")

    record = db.scalar(
        select(DesignFile)
        .join(Design, Design.id == DesignFile.design_id)
        .where(
            DesignFile.storage_path == path,
            Design.organization_id == context.org_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        data = get_storage().get(path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc

    return Response(
        content=data,
        media_type=record.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{record.filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )
