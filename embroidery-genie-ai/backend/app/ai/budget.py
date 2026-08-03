"""Cost budgets and threshold alerting.

Five scopes, checked before every call that would reach a provider: per
request, per user per day, per tenant per day, per tenant per month, and
globally per month. Each scope is expressed in two units — tokens and dollars —
and whichever binds first wins.

Two units, not one, because dollars only exist for models with a configured
rate. If someone points the app at an unpriced model, the dollar budgets go
quiet; the token budgets do not. The safety net that always holds is the one
denominated in the thing we can always count.

When a budget is exhausted the call does not happen. It is not queued, not
degraded, not retried later — the caller gets a clear reason and the product
falls back to its deterministic path. Failing safe, from a cost perspective,
means spending nothing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai import ledger
from app.core.config import settings

log = logging.getLogger(__name__)

#: Warn at these fractions of a limit; stop at 1.0.
ALERT_THRESHOLDS = (0.5, 0.8, 1.0)


@dataclass(frozen=True)
class Scope:
    name: str
    unit: str          # "tokens" | "usd"
    used: float
    limit: float       # 0 = no limit
    label: str

    @property
    def enforced(self) -> bool:
        return self.limit > 0

    @property
    def fraction(self) -> float | None:
        if not self.enforced:
            return None
        return self.used / self.limit

    @property
    def exceeded(self) -> bool:
        return self.enforced and self.used >= self.limit

    @property
    def alert_level(self) -> float | None:
        fraction = self.fraction
        if fraction is None:
            return None
        crossed = [t for t in ALERT_THRESHOLDS if fraction >= t]
        return max(crossed) if crossed else None

    def to_dict(self) -> dict:
        return {
            "scope": self.name,
            "label": self.label,
            "unit": self.unit,
            "used": round(self.used, 8) if self.unit == "usd" else int(self.used),
            "limit": self.limit if self.enforced else None,
            "enforced": self.enforced,
            "fraction_used": round(self.fraction, 4) if self.fraction is not None else None,
            "exceeded": self.exceeded,
            "alert_level": self.alert_level,
        }


@dataclass
class BudgetDecision:
    allowed: bool
    scopes: list[Scope] = field(default_factory=list)
    blocking: Scope | None = None
    overridden: bool = False

    @property
    def reason_code(self) -> str | None:
        if self.allowed or self.blocking is None:
            return None
        return f"budget_exceeded_{self.blocking.name}"

    @property
    def message(self) -> str:
        if self.allowed or self.blocking is None:
            return ""
        return _MESSAGES[self.blocking.name].format(
            used=_fmt(self.blocking), limit=_fmt(self.blocking, self.blocking.limit)
        )

    def alerts(self) -> list[dict]:
        return [
            {**scope.to_dict(), "severity": _severity(scope.alert_level)}
            for scope in self.scopes
            if scope.alert_level is not None
        ]


def _fmt(scope: Scope, value: float | None = None) -> str:
    amount = scope.used if value is None else value
    if scope.unit == "usd":
        return f"${amount:,.2f}"
    return f"{int(amount):,} tokens"


def _severity(level: float | None) -> str:
    if level is None:
        return "ok"
    if level >= 1.0:
        return "critical"
    if level >= 0.8:
        return "warning"
    return "notice"


_MESSAGES = {
    "user_daily_tokens": (
        "You have used your AI analysis allowance for today ({used} of {limit}). "
        "Design analysis, digitizing, pricing and export all still work — only "
        "the AI artwork description is paused. It resets at midnight UTC."
    ),
    "user_daily_cost": (
        "You have used your AI analysis allowance for today ({used} of {limit}). "
        "Everything except the AI artwork description still works; it resets at "
        "midnight UTC."
    ),
    "tenant_daily_tokens": (
        "This workspace has used its AI allowance for today ({used} of {limit}). "
        "Digitizing, pricing and export are unaffected. It resets at midnight UTC."
    ),
    "tenant_daily_cost": (
        "This workspace has used its AI allowance for today ({used} of {limit}). "
        "Digitizing, pricing and export are unaffected. It resets at midnight UTC."
    ),
    "tenant_monthly_tokens": (
        "This workspace has reached its AI budget for the month ({used} of "
        "{limit}). Digitizing, pricing and export continue to work. Ask an "
        "administrator to raise AI_MONTHLY_TOKENS_PER_TENANT if you need more."
    ),
    "tenant_monthly_cost": (
        "This workspace has reached its AI budget for the month ({used} of "
        "{limit}). Digitizing, pricing and export continue to work. Ask an "
        "administrator to raise AI_MONTHLY_COST_PER_TENANT_USD if you need more."
    ),
    "global_monthly_tokens": (
        "AI analysis is paused: the service-wide monthly AI budget is spent "
        "({used} of {limit}). Everything else is unaffected. An administrator "
        "must raise the limit or set AI_BUDGET_OVERRIDE."
    ),
    "global_monthly_cost": (
        "AI analysis is paused: the service-wide monthly AI budget is spent "
        "({used} of {limit}). Everything else is unaffected. An administrator "
        "must raise the limit or set AI_BUDGET_OVERRIDE."
    ),
}


def collect_scopes(
    db: Session,
    organization_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> list[Scope]:
    """Current consumption against every configured limit."""
    user_tokens, user_cost = ledger.user_day(db, user_id)
    day_tokens, day_cost = ledger.tenant_day(db, organization_id)
    month_tokens, month_cost = ledger.tenant_month(db, organization_id)
    all_tokens, all_cost = ledger.global_month(db)

    return [
        Scope("user_daily_tokens", "tokens", user_tokens,
              settings.ai_daily_tokens_per_user, "Your AI tokens today"),
        Scope("user_daily_cost", "usd", user_cost,
              settings.ai_daily_cost_per_user_usd, "Your AI spend today"),
        Scope("tenant_daily_tokens", "tokens", day_tokens,
              settings.ai_daily_tokens_per_tenant, "Workspace AI tokens today"),
        Scope("tenant_daily_cost", "usd", day_cost,
              settings.ai_daily_cost_per_tenant_usd, "Workspace AI spend today"),
        Scope("tenant_monthly_tokens", "tokens", month_tokens,
              settings.ai_monthly_tokens_per_tenant, "Workspace AI tokens this month"),
        Scope("tenant_monthly_cost", "usd", month_cost,
              settings.ai_monthly_cost_per_tenant_usd, "Workspace AI spend this month"),
        Scope("global_monthly_tokens", "tokens", all_tokens,
              settings.ai_monthly_tokens_global, "Service-wide AI tokens this month"),
        Scope("global_monthly_cost", "usd", all_cost,
              settings.ai_monthly_cost_global_usd, "Service-wide AI spend this month"),
    ]


def check(
    db: Session,
    organization_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> BudgetDecision:
    """Decide whether one more call may be made.

    The check is against spend *already recorded*, so a call is allowed if there
    is any headroom left and the last call of the period may overshoot slightly.
    That is the correct trade: refusing on a projection would deny service on an
    estimate, while overshooting is bounded by the per-request ceiling.
    """
    scopes = collect_scopes(db, organization_id, user_id)
    blocking = next((scope for scope in scopes if scope.exceeded), None)

    for scope in scopes:
        level = scope.alert_level
        if level is not None and level >= 0.8:
            log.warning(
                "AI budget alert [%s]: %s of %s (%.0f%%) — org=%s user=%s",
                _severity(level), _fmt(scope), _fmt(scope, scope.limit),
                (scope.fraction or 0) * 100, organization_id, user_id,
            )

    if blocking is None:
        return BudgetDecision(allowed=True, scopes=scopes)

    if settings.ai_budget_override:
        log.warning(
            "AI budget %s is exhausted but AI_BUDGET_OVERRIDE is set — continuing "
            "to spend deliberately.", blocking.name,
        )
        return BudgetDecision(allowed=True, scopes=scopes, blocking=blocking, overridden=True)

    return BudgetDecision(allowed=False, scopes=scopes, blocking=blocking)


def limits() -> dict:
    """The configured budget, for the dashboard and the cost report."""
    return {
        "per_request_input_tokens": settings.ai_max_input_tokens,
        "per_request_output_tokens": settings.ai_max_output_tokens,
        "user_daily_tokens": settings.ai_daily_tokens_per_user or None,
        "user_daily_cost_usd": settings.ai_daily_cost_per_user_usd or None,
        "tenant_daily_tokens": settings.ai_daily_tokens_per_tenant or None,
        "tenant_daily_cost_usd": settings.ai_daily_cost_per_tenant_usd or None,
        "tenant_monthly_tokens": settings.ai_monthly_tokens_per_tenant or None,
        "tenant_monthly_cost_usd": settings.ai_monthly_cost_per_tenant_usd or None,
        "global_monthly_tokens": settings.ai_monthly_tokens_global or None,
        "global_monthly_cost_usd": settings.ai_monthly_cost_global_usd or None,
        "override_active": settings.ai_budget_override,
        "beta_cost_mode": settings.ai_beta_cost_mode,
        "alert_thresholds": list(ALERT_THRESHOLDS),
    }
