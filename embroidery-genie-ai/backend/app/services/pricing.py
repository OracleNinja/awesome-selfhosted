"""Module 9 — AI Pricing Assistant.

Costs a job the way a shop actually does: build the true unit cost from
machine time, thread, labour and the blank, then apply margin.  The industry
habit of "$1 per thousand stitches" is a heuristic that hides money — this
computes the number underneath it and shows the heuristic alongside so an
operator can sanity-check.

Everything is deterministic. Nothing here calls a model: pricing a job from a
language model's intuition is how shops lose money.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings
from app.embroidery.digitizer import estimate_runtime_minutes
from app.embroidery.fabrics import get_fabric

# Handling minutes per piece: hooping, loading, unloading, trimming, folding.
# Varies far more by garment than by stitch count.
HANDLING_MINUTES = {
    "cotton_shirt": 1.6,
    "polyester_shirt": 1.6,
    "hoodie": 2.4,
    "hat": 1.2,
    "beanie": 1.8,
    "leather_patch": 0.9,
    "canvas": 2.0,
    "towel": 2.2,
}

# Thread on the cone: a 5,000 m cone of 40wt poly runs about $4.50, and the
# bobbin adds roughly 30% more thread consumption.
THREAD_COST_PER_1000M = 0.90
BOBBIN_FACTOR = 1.30

# Quantity breaks: setup amortises, and the operator gets into a rhythm.
QUANTITY_BREAKS = [
    (1, 1.00), (6, 0.92), (12, 0.85), (24, 0.78),
    (48, 0.72), (72, 0.68), (144, 0.62), (288, 0.56),
]


@dataclass
class PricingInputs:
    stitch_count: int
    color_count: int = 1
    trim_count: int = 0
    quantity: int = 1
    blank_cost: float = 0.0
    fabric: str = "cotton_shirt"
    machine_speed_spm: int = 800
    heads: int = 1
    labor_rate_per_hour: float = field(default_factory=lambda: settings.default_labor_rate_per_hour)
    machine_rate_per_hour: float = field(
        default_factory=lambda: settings.default_machine_rate_per_hour
    )
    digitizing_fee: float = field(default_factory=lambda: settings.default_digitizing_fee)
    digitizing_already_paid: bool = False
    thread_length_m: float | None = None
    wholesale_margin: float = field(default_factory=lambda: settings.default_wholesale_margin)
    retail_margin: float = field(default_factory=lambda: settings.default_retail_margin)
    rush: bool = False
    rush_surcharge: float = 0.25
    waste_rate: float = 0.03          # ruined blanks / misprints
    overhead_rate: float = 0.12       # rent, power, software, admin
    currency: str = field(default_factory=lambda: settings.default_currency)


@dataclass
class PricingResult:
    unit_cost: float
    unit_wholesale: float
    unit_retail: float
    total_cost: float
    total_wholesale: float
    total_retail: float
    breakdown: dict
    margins: dict
    quantity_breaks: list[dict]
    assumptions: list[str]
    currency: str

    def to_dict(self) -> dict:
        return {
            "unit_cost": self.unit_cost,
            "unit_wholesale": self.unit_wholesale,
            "unit_retail": self.unit_retail,
            "total_cost": self.total_cost,
            "total_wholesale": self.total_wholesale,
            "total_retail": self.total_retail,
            "breakdown": self.breakdown,
            "margins": self.margins,
            "quantity_breaks": self.quantity_breaks,
            "assumptions": self.assumptions,
            "currency": self.currency,
        }


def _round_money(value: float) -> float:
    return round(value + 1e-9, 2)


def _price_from_margin(cost: float, margin: float) -> float:
    """Margin is on the *selling price*, which is how a shop's books work.
    A 35% margin on a $10 cost is $15.38, not $13.50."""
    margin = max(0.0, min(0.95, margin))
    return cost / (1.0 - margin) if margin < 1 else cost


def quantity_multiplier(quantity: int) -> float:
    multiplier = 1.0
    for threshold, factor in QUANTITY_BREAKS:
        if quantity >= threshold:
            multiplier = factor
    return multiplier


def calculate(inputs: PricingInputs) -> PricingResult:
    fabric = get_fabric(inputs.fabric)
    quantity = max(1, int(inputs.quantity))

    # --- machine time -------------------------------------------------------
    sew_minutes = estimate_runtime_minutes(
        inputs.stitch_count,
        max(0, inputs.color_count - 1),
        inputs.trim_count,
        speed_spm=inputs.machine_speed_spm,
        speed_factor=fabric.speed_factor,
    )
    handling_minutes = HANDLING_MINUTES.get(inputs.fabric, 1.6)
    heads = max(1, int(inputs.heads))
    # A multi-head runs pieces in parallel; handling does not parallelise.
    machine_minutes_per_piece = sew_minutes / heads
    labor_minutes_per_piece = handling_minutes + (sew_minutes / heads) * 0.35

    machine_cost = machine_minutes_per_piece / 60.0 * inputs.machine_rate_per_hour
    labor_cost = labor_minutes_per_piece / 60.0 * inputs.labor_rate_per_hour

    # --- thread -------------------------------------------------------------
    if inputs.thread_length_m is not None:
        thread_m = inputs.thread_length_m
    else:
        # Rule of thumb: ~4.8 m of top thread per 1,000 stitches at 4 mm avg.
        thread_m = inputs.stitch_count / 1000.0 * 4.8
    thread_cost = thread_m * BOBBIN_FACTOR / 1000.0 * THREAD_COST_PER_1000M

    # --- blanks and waste ---------------------------------------------------
    blank_cost = max(0.0, inputs.blank_cost)
    waste_cost = (blank_cost + thread_cost) * max(0.0, inputs.waste_rate)

    direct_cost = machine_cost + labor_cost + thread_cost + blank_cost + waste_cost
    overhead_cost = direct_cost * max(0.0, inputs.overhead_rate)
    unit_cost_before_setup = direct_cost + overhead_cost

    # --- one-off setup ------------------------------------------------------
    setup_fee = 0.0 if inputs.digitizing_already_paid else max(0.0, inputs.digitizing_fee)
    setup_per_unit = setup_fee / quantity

    unit_cost = unit_cost_before_setup + setup_per_unit
    if inputs.rush:
        unit_cost *= 1.0 + max(0.0, inputs.rush_surcharge)

    # --- pricing ------------------------------------------------------------
    break_multiplier = quantity_multiplier(quantity)
    unit_wholesale = _price_from_margin(unit_cost, inputs.wholesale_margin) * break_multiplier
    unit_retail = _price_from_margin(unit_cost, inputs.retail_margin)

    # Never sell below cost, whatever the quantity break says.
    unit_wholesale = max(unit_wholesale, unit_cost * 1.05)
    unit_retail = max(unit_retail, unit_wholesale * 1.05)

    breakdown = {
        "machine_time": {
            "minutes_per_piece": round(machine_minutes_per_piece, 2),
            "sew_minutes": round(sew_minutes, 2),
            "cost": _round_money(machine_cost),
            "rate_per_hour": inputs.machine_rate_per_hour,
            "heads": heads,
        },
        "labor": {
            "minutes_per_piece": round(labor_minutes_per_piece, 2),
            "handling_minutes": handling_minutes,
            "cost": _round_money(labor_cost),
            "rate_per_hour": inputs.labor_rate_per_hour,
        },
        "thread": {
            "meters": round(thread_m * BOBBIN_FACTOR, 1),
            "cost": _round_money(thread_cost),
        },
        "blank": {"cost": _round_money(blank_cost)},
        "waste": {"rate": inputs.waste_rate, "cost": _round_money(waste_cost)},
        "overhead": {"rate": inputs.overhead_rate, "cost": _round_money(overhead_cost)},
        "setup": {
            "digitizing_fee": _round_money(setup_fee),
            "per_unit": _round_money(setup_per_unit),
            "already_paid": inputs.digitizing_already_paid,
        },
        "rush": {"applied": inputs.rush, "surcharge": inputs.rush_surcharge if inputs.rush else 0.0},
    }

    margins = {
        "wholesale_margin_pct": round((1 - unit_cost / unit_wholesale) * 100, 1)
        if unit_wholesale
        else 0.0,
        "retail_margin_pct": round((1 - unit_cost / unit_retail) * 100, 1) if unit_retail else 0.0,
        "wholesale_profit_per_unit": _round_money(unit_wholesale - unit_cost),
        "retail_profit_per_unit": _round_money(unit_retail - unit_cost),
        "wholesale_profit_total": _round_money((unit_wholesale - unit_cost) * quantity),
        "retail_profit_total": _round_money((unit_retail - unit_cost) * quantity),
    }

    breaks: list[dict] = []
    for threshold, _factor in QUANTITY_BREAKS:
        if threshold > 500:
            continue
        cost_at = unit_cost_before_setup + setup_fee / threshold
        if inputs.rush:
            cost_at *= 1.0 + inputs.rush_surcharge
        price_at = max(
            _price_from_margin(cost_at, inputs.wholesale_margin) * quantity_multiplier(threshold),
            cost_at * 1.05,
        )
        breaks.append({
            "quantity": threshold,
            "unit_cost": _round_money(cost_at),
            "unit_price": _round_money(price_at),
            "total": _round_money(price_at * threshold),
            "margin_pct": round((1 - cost_at / price_at) * 100, 1) if price_at else 0.0,
        })

    per_1000 = (unit_cost - setup_per_unit) / max(inputs.stitch_count / 1000.0, 0.001)
    assumptions = [
        f"{inputs.machine_speed_spm:,} spm head derated to "
        f"{int(inputs.machine_speed_spm * fabric.speed_factor):,} spm for {fabric.name}.",
        f"{handling_minutes} min handling per piece ({fabric.name}).",
        f"Thread at ${THREAD_COST_PER_1000M:.2f} per 1,000 m plus {int((BOBBIN_FACTOR - 1) * 100)}% "
        "for bobbin consumption.",
        f"{int(inputs.waste_rate * 100)}% waste allowance and "
        f"{int(inputs.overhead_rate * 100)}% overhead on direct cost.",
        f"Margins are on selling price: {int(inputs.wholesale_margin * 100)}% wholesale, "
        f"{int(inputs.retail_margin * 100)}% retail.",
        f"Implied run rate excluding setup: ${per_1000:.2f} per 1,000 stitches.",
    ]
    if inputs.rush:
        assumptions.append(f"Rush surcharge of {int(inputs.rush_surcharge * 100)}% applied.")
    if heads > 1:
        assumptions.append(f"{heads}-head machine: sew time divided across heads.")

    return PricingResult(
        unit_cost=_round_money(unit_cost),
        unit_wholesale=_round_money(unit_wholesale),
        unit_retail=_round_money(unit_retail),
        total_cost=_round_money(unit_cost * quantity),
        total_wholesale=_round_money(unit_wholesale * quantity),
        total_retail=_round_money(unit_retail * quantity),
        breakdown=breakdown,
        margins=margins,
        quantity_breaks=breaks,
        assumptions=assumptions,
        currency=inputs.currency,
    )
