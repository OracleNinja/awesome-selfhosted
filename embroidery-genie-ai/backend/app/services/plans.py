"""Subscription plans and quota enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Organization, PlanTier, Subscription, UsageEvent


@dataclass(frozen=True)
class Plan:
    tier: PlanTier
    name: str
    price_monthly: float
    designs_per_month: int          # 0 = unlimited
    seats: int
    export_formats: tuple[str, ...]
    features: tuple[str, ...]
    commercial_license: bool
    production_module: bool
    priority_processing: bool = False

    @property
    def unlimited(self) -> bool:
        return self.designs_per_month == 0

    def to_dict(self) -> dict:
        return {
            "tier": self.tier.value,
            "name": self.name,
            "price_monthly": self.price_monthly,
            "designs_per_month": self.designs_per_month,
            "unlimited": self.unlimited,
            "seats": self.seats,
            "export_formats": list(self.export_formats),
            "features": list(self.features),
            "commercial_license": self.commercial_license,
            "production_module": self.production_module,
            "priority_processing": self.priority_processing,
        }


ALL_FORMATS = ("dst", "pes", "jef", "exp", "vp3", "xxx", "u01", "pec")

PLANS: dict[PlanTier, Plan] = {
    PlanTier.free: Plan(
        tier=PlanTier.free,
        name="Free",
        price_monthly=0.0,
        designs_per_month=settings.free_designs_per_month,
        seats=1,
        export_formats=("dst", "pes"),
        features=(
            "5 designs per month",
            "AI design analysis",
            "Auto-digitizing",
            "DST and PES export",
            "Stitch simulator",
        ),
        commercial_license=False,
        production_module=False,
    ),
    PlanTier.pro: Plan(
        tier=PlanTier.pro,
        name="Pro",
        price_monthly=39.0,
        designs_per_month=settings.pro_designs_per_month,
        seats=1,
        export_formats=ALL_FORMATS,
        features=(
            "Unlimited designs",
            "All machine formats",
            "Commercial use licence",
            "Thread charts and production notes",
            "Customer mockups",
            "Custom thread catalogues",
        ),
        commercial_license=True,
        production_module=False,
        priority_processing=True,
    ),
    PlanTier.business: Plan(
        tier=PlanTier.business,
        name="Business",
        price_monthly=99.0,
        designs_per_month=settings.business_designs_per_month,
        seats=10,
        export_formats=ALL_FORMATS,
        features=(
            "Everything in Pro",
            "Team accounts (10 seats)",
            "Production management",
            "Orders, invoicing and inventory",
            "AI pricing assistant",
            "Machine profiles and scheduling",
        ),
        commercial_license=True,
        production_module=True,
        priority_processing=True,
    ),
}


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int, tier: PlanTier):
        self.used = used
        self.limit = limit
        self.tier = tier
        super().__init__(
            f"You have used all {limit} designs on the {PLANS[tier].name} plan this "
            f"period. Upgrade to Pro for unlimited designs."
        )


class FeatureLocked(Exception):
    def __init__(self, feature: str, tier: PlanTier, required: PlanTier):
        self.feature = feature
        super().__init__(
            f"{feature} is not included in the {PLANS[tier].name} plan. "
            f"Upgrade to {PLANS[required].name} to unlock it."
        )


def get_plan(tier: PlanTier | str) -> Plan:
    if isinstance(tier, str):
        tier = PlanTier(tier)
    return PLANS[tier]


def subscription_for(db: Session, organization_id) -> Subscription:
    """Every org has a subscription; free is created lazily on first access."""
    subscription = db.scalar(
        select(Subscription).where(Subscription.organization_id == organization_id)
    )
    if subscription is None:
        subscription = Subscription(organization_id=organization_id, tier=PlanTier.free)
        db.add(subscription)
        db.flush()
    return subscription


def period_start(subscription: Subscription) -> datetime:
    """Billing period start, falling back to a rolling 30-day window."""
    if subscription.current_period_start:
        return subscription.current_period_start
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_in_period(db: Session, organization_id, kind: str, since: datetime) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.kind == kind,
            UsageEvent.occurred_at >= since,
        )
    )
    return int(total or 0)


def quota_status(db: Session, organization_id) -> dict:
    subscription = subscription_for(db, organization_id)
    plan = get_plan(subscription.tier)
    since = period_start(subscription)
    used = usage_in_period(db, organization_id, "design", since)
    remaining = None if plan.unlimited else max(0, plan.designs_per_month - used)
    return {
        "tier": plan.tier.value,
        "plan": plan.to_dict(),
        "status": subscription.status.value,
        "period_start": since.isoformat(),
        "period_end": (
            subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else (since + timedelta(days=31)).isoformat()
        ),
        "designs_used": used,
        "designs_limit": None if plan.unlimited else plan.designs_per_month,
        "designs_remaining": remaining,
        "seats": subscription.seats,
    }


def assert_can_create_design(db: Session, organization_id) -> None:
    subscription = subscription_for(db, organization_id)
    plan = get_plan(subscription.tier)
    if plan.unlimited:
        return
    used = usage_in_period(db, organization_id, "design", period_start(subscription))
    if used >= plan.designs_per_month:
        raise QuotaExceeded(used, plan.designs_per_month, plan.tier)


def assert_format_allowed(db: Session, organization_id, extension: str) -> None:
    subscription = subscription_for(db, organization_id)
    plan = get_plan(subscription.tier)
    if extension.lower() not in plan.export_formats:
        raise FeatureLocked(f".{extension.upper()} export", plan.tier, PlanTier.pro)


def assert_production_module(db: Session, organization_id) -> None:
    subscription = subscription_for(db, organization_id)
    plan = get_plan(subscription.tier)
    if not plan.production_module:
        raise FeatureLocked("Production management", plan.tier, PlanTier.business)


def record_usage(db: Session, organization_id, user_id, kind: str, quantity: int = 1, **meta) -> None:
    db.add(
        UsageEvent(
            organization_id=organization_id,
            user_id=user_id,
            kind=kind,
            quantity=quantity,
            meta=meta or {},
        )
    )


def list_plans() -> list[dict]:
    return [plan.to_dict() for plan in PLANS.values()]
