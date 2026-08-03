"""AI cost dashboard.

The response has two halves and they are never mixed:

``recorded``    Sums straight out of ``ai_call_events``. Every figure is a count
                of something that happened. Nothing here is inferred.
``calculated``  Ratios derived from ``recorded`` — cost per design, hit rate,
                averages. Each one names the numerator and denominator it came
                from so it can be checked by hand.

Money in either half is an *estimate*, computed from the operator-configured
rate file, and every payload says so. Calls against a model with no configured
rate contribute tokens and are counted separately as ``unpriced_calls`` — they
are not silently valued at zero, because a zero would understate the bill and
poison every average built on it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Numeric, cast, func, select

from app.ai import budget, ledger, pricing
from app.ai.operations import DETERMINISTIC_BY_DESIGN, inventory
from app.ai.routing import routing_table
from app.core.config import settings
from app.core.deps import CurrentContext, DbSession
from app.models import AICallEvent, AIResultCache, Design, OrgRole, UsageEvent

router = APIRouter(prefix="/ai", tags=["ai"])

BILLABLE = (ledger.SUCCESS, ledger.FAILURE)


def _window_start(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    if period == "day":
        return ledger.start_of_day(now)
    if period == "week":
        return ledger.start_of_day(now) - timedelta(days=6)
    return ledger.start_of_month(now)


def _cost_sum():
    return func.coalesce(func.sum(cast(AICallEvent.estimated_cost_usd, Numeric(18, 8))), 0)


def _recorded(db, since: datetime, organization_id: uuid.UUID | None) -> dict:
    """Straight sums. Nothing derived, nothing assumed."""

    def scoped(query):
        query = query.where(AICallEvent.occurred_at >= since)
        if organization_id is not None:
            query = query.where(AICallEvent.organization_id == organization_id)
        return query

    totals = db.execute(
        scoped(
            select(
                func.count(AICallEvent.id),
                func.coalesce(func.sum(AICallEvent.input_tokens), 0),
                func.coalesce(func.sum(AICallEvent.output_tokens), 0),
                func.coalesce(func.sum(AICallEvent.cache_read_tokens), 0),
                func.coalesce(func.sum(AICallEvent.cache_write_tokens), 0),
                _cost_sum(),
            )
        )
    ).one()

    by_outcome = {
        row[0]: row[1]
        for row in db.execute(
            scoped(select(AICallEvent.outcome, func.count(AICallEvent.id)))
            .group_by(AICallEvent.outcome)
        ).all()
    }

    by_operation = [
        {
            "operation": row[0], "calls": row[1], "tokens": int(row[2] or 0),
            "estimated_cost_usd": float(row[3] or 0.0),
        }
        for row in db.execute(
            scoped(
                select(
                    AICallEvent.operation,
                    func.count(AICallEvent.id),
                    func.coalesce(
                        func.sum(AICallEvent.input_tokens + AICallEvent.output_tokens), 0
                    ),
                    _cost_sum(),
                )
            ).group_by(AICallEvent.operation)
        ).all()
    ]

    by_model = [
        {
            "model": row[0], "provider": row[1], "calls": row[2],
            "tokens": int(row[3] or 0), "estimated_cost_usd": float(row[4] or 0.0),
            "priced": pricing.table().rate_for(row[0]) is not None if row[0] else False,
        }
        for row in db.execute(
            scoped(
                select(
                    AICallEvent.model, AICallEvent.provider, func.count(AICallEvent.id),
                    func.coalesce(
                        func.sum(AICallEvent.input_tokens + AICallEvent.output_tokens), 0
                    ),
                    _cost_sum(),
                )
            ).where(AICallEvent.model.isnot(None)).group_by(
                AICallEvent.model, AICallEvent.provider
            )
        ).all()
    ]

    by_tenant = []
    if organization_id is None:
        by_tenant = [
            {
                "organization_id": str(row[0]) if row[0] else None,
                "calls": row[1], "tokens": int(row[2] or 0),
                "estimated_cost_usd": float(row[3] or 0.0),
            }
            for row in db.execute(
                scoped(
                    select(
                        AICallEvent.organization_id, func.count(AICallEvent.id),
                        func.coalesce(
                            func.sum(AICallEvent.input_tokens + AICallEvent.output_tokens), 0
                        ),
                        _cost_sum(),
                    )
                ).group_by(AICallEvent.organization_id)
                .order_by(_cost_sum().desc())
                .limit(50)
            ).all()
        ]

    latency = db.execute(
        scoped(
            select(
                func.avg(AICallEvent.latency_ms), func.max(AICallEvent.latency_ms)
            )
        ).where(AICallEvent.latency_ms.isnot(None), AICallEvent.outcome.in_(BILLABLE))
    ).one()

    unpriced = db.scalar(
        scoped(select(func.count(AICallEvent.id)))
        .where(
            AICallEvent.outcome.in_(BILLABLE),
            AICallEvent.estimated_cost_usd.is_(None),
        )
    )

    failure_reasons = [
        {"reason": row[0], "count": row[1]}
        for row in db.execute(
            scoped(select(AICallEvent.reason, func.count(AICallEvent.id)))
            .where(AICallEvent.outcome.in_((ledger.FAILURE, ledger.BLOCKED, ledger.SKIPPED)))
            .group_by(AICallEvent.reason)
            .order_by(func.count(AICallEvent.id).desc())
            .limit(15)
        ).all()
    ]

    # Denominators come from the product's own meter, not from the AI ledger.
    usage_query = select(
        func.coalesce(func.sum(UsageEvent.quantity), 0)
    ).where(UsageEvent.occurred_at >= since)
    if organization_id is not None:
        usage_query = usage_query.where(UsageEvent.organization_id == organization_id)

    designs = int(db.scalar(usage_query.where(UsageEvent.kind == "design")) or 0)
    exports = int(db.scalar(usage_query.where(UsageEvent.kind == "export")) or 0)

    design_query = select(func.count(Design.id)).where(Design.created_at >= since)
    if organization_id is not None:
        design_query = design_query.where(Design.organization_id == organization_id)

    cache_query = select(
        func.count(AIResultCache.id), func.coalesce(func.sum(AIResultCache.hit_count), 0)
    )
    if organization_id is not None:
        cache_query = cache_query.where(AIResultCache.organization_id == organization_id)
    cache_entries, cache_hits_lifetime = db.execute(cache_query).one()

    return {
        "_note": "Every figure below is a sum of rows recorded at the time the "
                 "call happened. None of it is modelled or projected.",
        "window_start": since.isoformat(),
        "window_end": datetime.now(timezone.utc).isoformat(),
        "total_ledger_rows": int(totals[0] or 0),
        "input_tokens": int(totals[1] or 0),
        "output_tokens": int(totals[2] or 0),
        "cache_read_tokens": int(totals[3] or 0),
        "cache_write_tokens": int(totals[4] or 0),
        "total_tokens": int(sum(int(t or 0) for t in totals[1:5])),
        "estimated_cost_usd": float(totals[5] or 0.0),
        "calls_by_outcome": {
            "success": int(by_outcome.get(ledger.SUCCESS, 0)),
            "failure": int(by_outcome.get(ledger.FAILURE, 0)),
            "served_from_cache": int(by_outcome.get(ledger.CACHE_HIT, 0)),
            "blocked_by_budget": int(by_outcome.get(ledger.BLOCKED, 0)),
            "skipped": int(by_outcome.get(ledger.SKIPPED, 0)),
        },
        "unpriced_calls": int(unpriced or 0),
        "by_operation": by_operation,
        "by_model": by_model,
        "by_tenant": by_tenant,
        "failure_and_skip_reasons": failure_reasons,
        "latency_ms": {
            "average": round(float(latency[0]), 1) if latency[0] is not None else None,
            "max": int(latency[1]) if latency[1] is not None else None,
        },
        "designs_created": int(db.scalar(design_query) or 0),
        "design_usage_events": designs,
        "export_usage_events": exports,
        "cache_entries_stored": int(cache_entries or 0),
        "cache_hits_lifetime": int(cache_hits_lifetime or 0),
    }


def _calculated(recorded: dict) -> dict:
    """Ratios over the recorded figures, each showing its own arithmetic."""
    outcomes = recorded["calls_by_outcome"]
    reached_provider = outcomes["success"] + outcomes["failure"]
    served = reached_provider + outcomes["served_from_cache"]
    cost = recorded["estimated_cost_usd"]
    designs = recorded["design_usage_events"]
    exports = recorded["export_usage_events"]

    def ratio(numerator: float, denominator: float, formula: str) -> dict:
        return {
            "value": round(numerator / denominator, 6) if denominator else None,
            "formula": formula,
            "denominator": denominator,
            "computable": bool(denominator),
        }

    return {
        "_note": "Derived from the 'recorded' block above. A null value means the "
                 "denominator was zero — no data yet, not a result of zero.",
        "cache_hit_rate": ratio(
            outcomes["served_from_cache"], served,
            "served_from_cache / (success + failure + served_from_cache)",
        ),
        "provider_success_rate": ratio(
            outcomes["success"], reached_provider, "success / (success + failure)"
        ),
        "estimated_cost_per_design_usd": ratio(
            cost, designs, "estimated_cost_usd / designs metered in this window"
        ),
        "estimated_cost_per_export_usd": ratio(
            cost, exports, "estimated_cost_usd / exports metered in this window"
        ),
        "estimated_cost_per_provider_call_usd": ratio(
            cost, reached_provider, "estimated_cost_usd / (success + failure)"
        ),
        "average_tokens_per_provider_call": ratio(
            recorded["total_tokens"], reached_provider,
            "total_tokens / (success + failure)",
        ),
        "estimated_cost_per_successful_sewout_usd": {
            "value": None,
            "formula": "estimated_cost_usd / confirmed sew-outs",
            "denominator": 0,
            "computable": False,
            "note": "Sew-out confirmation is not yet recorded. This stays null "
                    "until beta operators report completed runs — it is not "
                    "estimated from order status.",
        },
        "cost_confidence": (
            "partial" if recorded["unpriced_calls"] else "full"
        ) if pricing.table().loaded else "none",
    }


@router.get("/costs")
def ai_costs(
    context: CurrentContext,
    db: DbSession,
    period: str = Query("month", pattern="^(day|week|month)$"),
    scope: str = Query("tenant", pattern="^(tenant|global)$"),
) -> dict:
    """AI spend for this workspace, or service-wide for a listed administrator."""
    if not context.can(OrgRole.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI cost figures are visible to workspace admins and owners.",
        )

    organization_id: uuid.UUID | None = context.org_id
    if scope == "global":
        allowed = {email.lower() for email in settings.ai_admin_emails}
        if (context.user.email or "").lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service-wide AI figures require an account listed in "
                       "AI_ADMIN_EMAILS.",
            )
        organization_id = None

    since = _window_start(period)
    recorded = _recorded(db, since, organization_id)
    decision = budget.check(db, context.org_id, context.user_id)

    return {
        "scope": scope,
        "period": period,
        "organization_id": str(organization_id) if organization_id else None,
        "recorded": recorded,
        "calculated": _calculated(recorded),
        "pricing": pricing.table().to_dict(),
        "budgets": budget.limits(),
        "budget_status": [scope_.to_dict() for scope_ in decision.scopes],
        "alerts": decision.alerts(),
        "ai_enabled": settings.ai_enabled,
        "ai_available": settings.ai_available(),
    }


@router.get("/inventory")
def ai_inventory(context: CurrentContext) -> dict:
    """Every AI operation in the product, and everything deliberately kept out.

    Readable by any member: knowing what the product sends to a model is not
    privileged information, it is the answer to a fair question.
    """
    return {
        "operations": inventory(),
        "routing": routing_table(),
        "deterministic_by_design": [
            {"capability": key, "implementation": value}
            for key, value in sorted(DETERMINISTIC_BY_DESIGN.items())
        ],
        "kill_switches": {
            "AI_ENABLED": settings.ai_enabled,
            "VISION_ANALYSIS_ENABLED": settings.vision_analysis_enabled,
            "AI_PROVIDER": settings.ai_provider,
            "AI_BETA_COST_MODE": settings.ai_beta_cost_mode,
            "AI_CACHE_ENABLED": settings.ai_cache_enabled,
        },
        "data_sent_to_providers": [
            "A downscaled, re-encoded copy of the uploaded artwork "
            f"(longest edge {settings.ai_image_max_edge_px} px, EXIF and colour "
            "profiles stripped).",
            "A fixed prompt. No customer name, design name, filename, order, "
            "price, workspace identifier or conversation history is included.",
        ],
    }
