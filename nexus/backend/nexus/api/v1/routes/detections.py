"""Detection findings, their evidence, and the detection engine's status.

Route order matters: ``/detections/status`` and ``/detections/config`` are
declared before ``/detections/{finding_id}``. FastAPI matches in declaration
order, and a literal path registered after a ``UUID`` parameter would be parsed
as a malformed id and answered with a confusing 422.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import select

from nexus.api.deps import ContextDep, SessionDep
from nexus.api.schemas.common import responses
from nexus.api.schemas.detection import (
    DetectionConfigResponse,
    DetectionStatusResponse,
    EvidenceResponse,
    FindingDetail,
    FindingPage,
    FindingSummary,
    TriageFindingRequest,
    UpdateDetectionConfigRequest,
)
from nexus.api.security import actor_from, require
from nexus.core.errors import Conflict, ValidationFailed
from nexus.core.rbac import Permission
from nexus.db.models.finding import Finding
from nexus.db.models.setting import AppSetting
from nexus.detection.config import SETTING_KEY, load_config, merge_config
from nexus.services import findings as finding_service
from nexus.services.audit import AuditAction, AuditService
from nexus.services.auth import AuthenticatedIdentity

router = APIRouter(prefix="/detections", tags=["detections"])

DetectionReaderDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.DETECTIONS_READ))]
TriageDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.DETECTIONS_TRIAGE))]
RulesReaderDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.RULES_READ))]
RulesWriterDep = Annotated[AuthenticatedIdentity, Depends(require(Permission.RULES_WRITE))]

MAX_PAGE_SIZE = 200


@router.get(
    "/status",
    response_model=DetectionStatusResponse,
    summary="Detection engine status",
    description=(
        "What the engine has actually done, and what it cannot do.\n\n"
        "`analysis.state` distinguishes **IDLE** (every stored event has been "
        "analysed — the healthy state, including after a finite laboratory "
        "scenario finishes) from **STALLED** (events are waiting and no pass "
        "has run recently, which usually means no worker process is running).\n\n"
        "Each detector is reported per scope. A detector with a missing "
        "prerequisite is **NOT_CONFIGURED** with a remedy — never omitted, and "
        "never reported as having found nothing."
    ),
    responses=responses(401, 403),
)
async def get_status(
    identity: DetectionReaderDep, session: SessionDep, context: ContextDep
) -> DetectionStatusResponse:
    loaded = await load_config(session)
    payload = await finding_service.detection_status(session, context.settings, loaded)
    return DetectionStatusResponse.model_validate(payload)


@router.get(
    "/config",
    response_model=DetectionConfigResponse,
    summary="Read the detection and risk configuration",
    description=(
        "The rule parameters and risk weights in force, with their source.\n\n"
        "`source` is **CONFIGURED** when a stored document is in use, "
        "**DEFAULT** when none has been saved, and "
        "**DEFAULT_AFTER_INVALID_CONFIGURATION** when a stored document was "
        "rejected — in which case `detail` names the offending field. The last "
        "case is reported rather than silently falling back, because an "
        "operator whose tuning was ignored needs to know."
    ),
    responses=responses(401, 403),
)
async def get_config(identity: RulesReaderDep, session: SessionDep) -> DetectionConfigResponse:
    loaded = await load_config(session)
    return DetectionConfigResponse(
        source=loaded.source,
        detail=loaded.detail,
        version=loaded.version,
        fingerprint=loaded.config.fingerprint(),
        config=loaded.config.model_dump(mode="json"),
    )


@router.put(
    "/config",
    response_model=DetectionConfigResponse,
    summary="Update the detection and risk configuration",
    description=(
        "Merges a partial document into the current configuration and validates "
        "the result as a whole, so a partial update cannot produce a document "
        "that would be rejected on load.\n\n"
        "Supply the `version` you read to get optimistic concurrency: a stale "
        "version is a `409 CONFLICT` rather than an overwrite of someone else's "
        "change. A written `reason` is required and is recorded in the audit log."
    ),
    responses=responses(401, 403, 409, 422),
)
async def update_config(
    payload: UpdateDetectionConfigRequest,
    identity: RulesWriterDep,
    session: SessionDep,
    context: ContextDep,
) -> DetectionConfigResponse:
    loaded = await load_config(session)

    stale = (
        payload.version is not None
        and loaded.version is not None
        and payload.version != loaded.version
    )
    if stale:
        raise Conflict(
            "The detection configuration changed since you read it.",
            details={"your_version": payload.version, "current_version": loaded.version},
        )

    try:
        updated = merge_config(loaded.config, payload.config)
    except ValidationError as exc:
        # The merged document is what would be stored, so it is validated as a
        # whole and rejected with the standard envelope. Raising pydantic's own
        # error here would reach the client as an unhandled 500 — the same class
        # of leak the error model exists to prevent.
        raise ValidationFailed(
            "The detection configuration is not valid.",
            details={"fields": [_field_error(error) for error in exc.errors()[:10]]},
        ) from exc

    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == SETTING_KEY))
    ).scalar_one_or_none()
    if row is None:
        row = AppSetting(
            key=SETTING_KEY,
            value=updated.model_dump(mode="json"),
            description="Detection rule parameters and risk weights.",
            updated_by_user_id=identity.user.id,
            version=1,
        )
        session.add(row)
    else:
        row.value = updated.model_dump(mode="json")
        row.updated_by_user_id = identity.user.id
        row.version = row.version + 1

    await session.flush()

    await AuditService(session, context.settings).record(
        AuditAction.DETECTION_CONFIG_CHANGED,
        actor=actor_from(identity),
        target_type="setting",
        target_id=SETTING_KEY,
        reason=payload.reason,
        details={
            "previous_fingerprint": loaded.config.fingerprint(),
            "fingerprint": updated.fingerprint(),
            "previous_source": loaded.source,
            "version": row.version,
            # The submitted keys, not their values: which knobs moved is the
            # auditable fact, and the full document is retrievable from the API.
            "changed_sections": sorted(payload.config),
        },
    )

    return DetectionConfigResponse(
        source="CONFIGURED",
        detail=None,
        version=row.version,
        fingerprint=updated.fingerprint(),
        config=updated.model_dump(mode="json"),
    )


@router.get(
    "",
    response_model=FindingPage,
    summary="Query detection findings",
    description=(
        "Most recently active first.\n\n"
        "**Simulated findings are excluded by default**, exactly as simulated "
        "events are: a finding derived from laboratory data is a real finding "
        "about an isolated environment, not an observation of your network.\n\n"
        "`severity` and `confidence` are separate axes and are never combined "
        "by the server. Filter on both if you want 'serious and certain'."
    ),
    responses=responses(401, 403, 422),
)
async def query_findings(
    identity: DetectionReaderDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    status: Annotated[str | None, Query(max_length=16)] = None,
    severity: Annotated[str | None, Query(max_length=16)] = None,
    detection_type: Annotated[str | None, Query(max_length=32)] = None,
    detector: Annotated[str | None, Query(max_length=64)] = None,
    device_id: Annotated[uuid.UUID | None, Query()] = None,
    min_confidence: Annotated[int | None, Query(ge=0, le=100)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    active_only: Annotated[bool, Query(description="Only OPEN and ACKNOWLEDGED findings.")] = False,
    include_simulation: Annotated[bool, Query()] = False,
    only_simulation: Annotated[bool, Query()] = False,
) -> FindingPage:
    filters = finding_service.FindingFilters(
        status=status,
        severity=severity,
        detection_type=detection_type,
        detector=detector,
        device_id=device_id,
        min_confidence=min_confidence,
        since=since,
        until=until,
        active_only=active_only,
        include_simulation=include_simulation,
        only_simulation=only_simulation,
    )
    rows, total = await finding_service.list_findings(session, filters, limit=limit, offset=offset)
    return FindingPage(
        items=[FindingSummary.model_validate(row) for row in rows],
        total=total,
        has_more=offset + len(rows) < total,
    )


@router.get(
    "/{finding_id}",
    response_model=FindingDetail,
    summary="Get one finding with its evidence and derivation",
    responses=responses(401, 403, 404),
)
async def get_finding(
    finding_id: uuid.UUID, identity: DetectionReaderDep, session: SessionDep
) -> FindingDetail:
    finding = await finding_service.get_finding(session, finding_id)
    _, available = await finding_service.evidence_for(session, finding)
    return _detail(finding, available)


@router.get(
    "/{finding_id}/evidence",
    response_model=EvidenceResponse,
    summary="Get the observations supporting a finding",
    description=(
        "Every linked observation with its provenance: which sensor produced "
        "it, when, and whether the pre-normalisation form is still stored.\n\n"
        "`evidence_state` reports **PARTIAL** or **PRUNED** when event retention "
        "has removed some of the underlying observations. The finding's stored "
        "`snapshot` survives that and is what keeps it explainable."
    ),
    responses=responses(401, 403, 404),
)
async def get_evidence(
    finding_id: uuid.UUID, identity: DetectionReaderDep, session: SessionDep
) -> EvidenceResponse:
    finding = await finding_service.get_finding(session, finding_id)
    observations, available = await finding_service.evidence_for(session, finding)

    snapshot = []
    if isinstance(finding.evidence, dict):
        stored = finding.evidence.get("observations")
        if isinstance(stored, list):
            snapshot = stored

    return EvidenceResponse(
        finding_id=finding.id,
        detection_type=finding.detection_type,
        explanation=finding.explanation,
        evidence=finding.evidence,
        evidence_state=finding_service.evidence_state(finding, available),
        observations_recorded=finding.observation_count,
        observations_available=available,
        observations=observations,
        snapshot=snapshot,
    )


@router.post(
    "/{finding_id}/status",
    response_model=FindingDetail,
    summary="Triage a finding",
    description=(
        "Records a human decision and re-derives the affected device's risk.\n\n"
        "**RESOLVED** — dealt with. The rule may reopen it if the same thing "
        "happens again.\n"
        "**SUPPRESSED** — do not raise this again; the rule will not reopen it.\n"
        "**FALSE_POSITIVE** — the rule was wrong. Kept distinct from the others "
        "because it is the only feedback signal about detector quality.\n\n"
        "All three stop the finding contributing to risk. A written `note` is "
        "required and is stored on the finding and in the audit log."
    ),
    responses=responses(401, 403, 404, 409, 422),
)
async def triage_finding(
    finding_id: uuid.UUID,
    payload: TriageFindingRequest,
    identity: TriageDep,
    session: SessionDep,
    context: ContextDep,
) -> FindingDetail:
    finding = await finding_service.get_finding(session, finding_id)
    await finding_service.triage(
        session,
        context.settings,
        finding,
        status=payload.status,
        note=payload.note,
        actor=actor_from(identity),
        user_id=identity.user.id,
    )
    # The UPDATE leaves `updated_at` expired, because its value is computed by
    # the server. Refreshing here loads it inside the async context; letting
    # serialisation trigger the lazy load instead raises MissingGreenlet, since
    # pydantic reads attributes synchronously.
    await session.refresh(finding)
    _, available = await finding_service.evidence_for(session, finding)
    return _detail(finding, available)


def _field_error(error: Any) -> dict[str, str]:
    """One validation error, without the submitted value.

    Same rule as the global validation handler: the location and the message
    are useful, and echoing what was submitted risks putting a secret into a
    response and a log.
    """
    return {
        "field": ".".join(str(part) for part in error.get("loc", ())) or "<document>",
        "message": str(error.get("msg", "invalid")),
    }


def _detail(finding: Finding, available: int) -> FindingDetail:
    """Assemble a detail response, including the two derived evidence fields.

    ``evidence_state`` and ``observations_available`` are not columns — they are
    the comparison between what the finding recorded and what survives retention
    — so they are computed here rather than defaulted on the schema, where a
    default would let a caller receive "COMPLETE" without anything having
    checked.
    """
    return FindingDetail(
        **FindingSummary.model_validate(finding).model_dump(),
        evidence=finding.evidence or {},
        risk_factors=finding.risk_factors or [],
        triaged_at=finding.triaged_at,
        triage_note=finding.triage_note,
        triaged_by_user_id=finding.triaged_by_user_id,
        evidence_state=finding_service.evidence_state(finding, available),
        observations_available=available,
    )
