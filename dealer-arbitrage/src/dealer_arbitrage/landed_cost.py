"""Landed-cost model.

Core principle 10/11: optimize for $0 LANDED, not $0 sticker. A vehicle offered
free with $1,400 of freight, prep and doc fees is not a free vehicle.

The one rule this module exists to enforce: an undisclosed component is NOT
zero. A quote missing its freight number is an incomplete quote, and the system
says so rather than quietly ranking it first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from . import db

# Ordered for presentation; each maps to an offers.* column.
COMPONENTS: tuple[tuple[str, str], ...] = (
    ("vehicle_price_cents", "Vehicle price"),
    ("freight_cents", "Freight / shipping"),
    ("setup_prep_cents", "Setup / PDI / assembly"),
    ("destination_cents", "Destination charge"),
    ("doc_fee_cents", "Documentation fee"),
    ("taxes_cents", "Taxes"),
    ("dealer_fees_cents", "Dealer fees"),
    ("required_purchase_cents", "Required purchase / minimum order"),
    ("other_fees_cents", "Other fees"),
)

# Components a counterparty must state before an offer is considered complete.
# Principle 11 names freight, setup, destination, doc fees, taxes, dealer fees and
# required purchases as part of total acquisition cost — so all of them must be
# explicitly confirmed (including confirmed at $0) before a total is claimed.
# `other_fees_cents` is the catch-all and stays optional.
MUST_DISCLOSE = ("vehicle_price_cents", "freight_cents", "setup_prep_cents",
                 "destination_cents", "doc_fee_cents", "taxes_cents",
                 "dealer_fees_cents", "required_purchase_cents")


def fmt(cents: int | None) -> str:
    if cents is None:
        return "UNDISCLOSED"
    if cents == 0:
        return "$0"
    return f"${cents / 100:,.2f}"


def parse_money(text: str | float | int | None) -> int | None:
    """'$1,250.00' -> 125000. None/blank/'unknown' -> None (not zero)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(round(float(text) * 100))
    cleaned = str(text).strip().lower().replace("$", "").replace(",", "").replace("usd", "")
    cleaned = cleaned.strip()
    if cleaned in ("", "unknown", "n/a", "na", "tbd", "-", "?"):
        return None
    if cleaned in ("free", "none", "no charge", "included", "waived", "0", "0.00"):
        return 0
    try:
        return int(round(float(cleaned) * 100))
    except ValueError:
        return None


@dataclass
class LandedCost:
    total_cents: int | None                 # None when any component is undisclosed
    known_minimum_cents: int                # sum of what has actually been disclosed
    undisclosed: list[str] = field(default_factory=list)
    breakdown: list[tuple[str, int | None]] = field(default_factory=list)
    credit_pct: int | None = None
    credit_conditions: str | None = None
    min_order_units: int | None = None

    @property
    def complete(self) -> bool:
        return self.total_cents is not None

    @property
    def is_zero(self) -> bool:
        return self.total_cents == 0

    @property
    def net_after_credit_cents(self) -> int | None:
        """Cost after a first-order credit is applied — CONDITIONAL, not banked.

        Returns None when the total is unknown. A 100% credit nets to zero only
        if the qualifying order is actually placed, which is why the conditions
        travel with the number everywhere it is displayed.
        """
        if self.total_cents is None or not self.credit_pct:
            return self.total_cents
        credited = int(round(self.total_cents * (self.credit_pct / 100)))
        return max(self.total_cents - credited, 0)

    def render(self) -> str:
        lines = ["  LANDED COST BREAKDOWN"]
        for label, cents in self.breakdown:
            lines.append(f"    {label:<38} {fmt(cents):>14}")
        lines.append(f"    {'-' * 38} {'-' * 14}")
        if self.complete:
            lines.append(f"    {'TOTAL LANDED COST':<38} {fmt(self.total_cents):>14}")
        else:
            lines.append(f"    {'DISCLOSED SO FAR (INCOMPLETE)':<38} "
                         f"{fmt(self.known_minimum_cents):>14}")
            lines.append(f"    !! Undisclosed: {', '.join(self.undisclosed)}")
            lines.append("    !! An undisclosed component is not $0. Ask before comparing.")
        if self.credit_pct:
            lines.append(f"    {'Credit toward first order':<38} {self.credit_pct:>13}%")
            if self.complete:
                lines.append(f"    {'NET IF CREDIT IS EARNED':<38} "
                             f"{fmt(self.net_after_credit_cents):>14}")
                today = fmt(self.total_cents)
            else:
                lines.append(f"    {'NET IF CREDIT IS EARNED':<38} "
                             f"{'unknown':>14}")
                today = f"at least {fmt(self.known_minimum_cents)}"
            if self.min_order_units:
                lines.append(f"    Conditional on an order of {self.min_order_units} units"
                             f"{' — ' + self.credit_conditions if self.credit_conditions else ''}")
            lines.append(f"    Note: credit is CONDITIONAL. Out-of-pocket today is {today}.")
        return "\n".join(lines)


def compute(offer: Mapping[str, Any] | Any) -> LandedCost:
    """Build a LandedCost from an offers row or an equivalent mapping."""
    breakdown: list[tuple[str, int | None]] = []
    undisclosed: list[str] = []
    known = 0
    complete = True

    for column, label in COMPONENTS:
        value = db.field(offer, column)
        value = value if value is None else int(value)
        breakdown.append((label, value))
        if value is None:
            if column in MUST_DISCLOSE:
                undisclosed.append(label)
                complete = False
        else:
            known += value

    credit_pct = db.field(offer, "credit_pct")
    return LandedCost(
        total_cents=known if complete else None,
        known_minimum_cents=known,
        undisclosed=undisclosed,
        breakdown=breakdown,
        credit_pct=int(credit_pct) if credit_pct is not None else None,
        credit_conditions=db.field(offer, "credit_conditions"),
        min_order_units=db.field(offer, "min_order_units"),
    )


def rank_key(offer: Mapping[str, Any] | Any) -> tuple[int, int, int]:
    """Sort key for offers: complete-and-cheap first, incomplete quotes last.

    Deliberately does not let an incomplete quote outrank a complete one, no
    matter how attractive the disclosed part looks.
    """
    cost = compute(offer)
    if cost.complete:
        return (0, cost.total_cents or 0, -(cost.credit_pct or 0))
    return (1, cost.known_minimum_cents, -(cost.credit_pct or 0))


def compare(a: Mapping[str, Any] | Any, b: Mapping[str, Any] | Any) -> str:
    ca, cb = compute(a), compute(b)
    parts = [f"A: {fmt(ca.total_cents) if ca.complete else fmt(ca.known_minimum_cents) + '+'}",
             f"B: {fmt(cb.total_cents) if cb.complete else fmt(cb.known_minimum_cents) + '+'}"]
    if ca.complete and cb.complete:
        delta = (ca.total_cents or 0) - (cb.total_cents or 0)
        parts.append(f"delta: {fmt(abs(delta))} in favour of {'B' if delta > 0 else 'A'}")
    else:
        parts.append("not directly comparable — at least one quote is incomplete")
    return " | ".join(parts)
