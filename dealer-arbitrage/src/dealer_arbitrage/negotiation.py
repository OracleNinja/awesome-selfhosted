"""Stage 9 — the negotiation engine.

Objective function: MINIMUM LANDED ACQUISITION COST. Not lowest sticker price,
not biggest discount percentage — lowest total out-of-pocket to get a unit
standing on our floor.

Two constraints shape every move it proposes:
  * Never claim leverage that does not exist. No invented competing offers, no
    invented order commitments. Any market price cited carries its source URL.
  * Never accept. The engine proposes; the human decides anything costing money.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import db, evidence, landed_cost, paths
from .strategy import StrategyEngine


@dataclass
class NegotiationMove:
    round_no: int
    ask: str
    ladder_item: str | None
    rationale: str
    talking_points: list[str] = field(default_factory=list)
    must_capture: list[str] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    concessions_available: list[str] = field(default_factory=list)
    walk_away: str | None = None
    requires_human: bool = True
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  NEGOTIATION MOVE — round {self.round_no}",
                 f"    ASK:       {self.ask}"]
        if self.ladder_item:
            lines.append(f"    LADDER:    {self.ladder_item}")
        lines.append(f"    WHY:       {self.rationale}")
        if self.talking_points:
            lines.append("    POINTS:")
            lines += [f"      - {p}" for p in self.talking_points]
        if self.must_capture:
            lines.append("    CAPTURE IN WRITING:")
            lines += [f"      - {c}" for c in self.must_capture]
        if self.citations:
            lines.append("    CITED PRICES (with sources):")
            lines += [f"      - {c['claim']} — {c['url']}" for c in self.citations]
        if self.concessions_available:
            lines.append("    WE CAN OFFER: " + "; ".join(self.concessions_available))
        if self.walk_away:
            lines.append(f"    WALK AWAY:  {self.walk_away}")
        for warning in self.warnings:
            lines.append(f"    !! {warning}")
        if self.requires_human:
            lines.append("    !! Requires approval before sending.")
        return "\n".join(lines)


class NegotiationEngine:
    def __init__(self, strategies: StrategyEngine | None = None,
                 settings: Mapping[str, Any] | None = None):
        self.strategies = strategies or StrategyEngine()
        self.settings = settings or json.loads(paths.SETTINGS.read_text(encoding="utf-8"))

    # ------------------------------------------------------------- analysis
    def analyze_offer(self, offer: Mapping[str, Any]) -> dict[str, Any]:
        cost = landed_cost.compute(offer)
        target_cost = self.settings["campaign"].get("target_landed_cost_cents", 0)
        gap = None if cost.total_cents is None else cost.total_cents - target_cost
        return {
            "cost": cost,
            "meets_objective": cost.complete and cost.total_cents <= target_cost,
            "gap_cents": gap,
            "net_after_credit": cost.net_after_credit_cents,
            "incomplete": not cost.complete,
        }

    def next_move(self, conn: sqlite3.Connection, target_id: int) -> NegotiationMove:
        row = db.one(conn, "SELECT t.*, c.name AS company, c.company_type"
                           " FROM targets t JOIN companies c ON c.id = t.company_id"
                           " WHERE t.id = ?", (target_id,))
        if row is None:
            raise ValueError(f"no target with id {target_id}")

        history = db.rows(conn, "SELECT * FROM negotiations WHERE target_id = ?"
                                " ORDER BY round_no", (target_id,))
        round_no = len(history) + 1
        refused = self._refused(history)
        secured = self._secured(history)

        offers = db.rows(conn, "SELECT * FROM offers WHERE target_id = ?"
                               " AND status IN ('open','countered')", (target_id,))
        best = min(offers, key=landed_cost.rank_key) if offers else None
        rung = self.strategies.next_concession(refused, secured)

        move = NegotiationMove(
            round_no=round_no, ask="", ladder_item=rung["label"] if rung else None,
            rationale="", concessions_available=self._our_concessions())

        if best is None:
            move.ask = ("Ask for the unit at no cost with freight included; name the "
                        "100%-credited structure as the immediate fallback.")
            move.rationale = ("No offer on the table yet — open at the top of the ladder "
                              "so there is room to come down.")
            move.must_capture = ["Whether a no-cost evaluation unit exists at all",
                                 "What their dealer-development program requires",
                                 "Full landed cost if a unit must be purchased"]
            move.talking_points = self._talking_points(row, None)
            return move

        analysis = self.analyze_offer(best)
        cost = analysis["cost"]

        if analysis["incomplete"]:
            move.ask = ("Ask for the complete landed cost broken out before discussing "
                        "anything else.")
            move.rationale = (f"The quote is missing {', '.join(cost.undisclosed)}. "
                              "An undisclosed component is not $0 and the offer cannot "
                              "be compared until it is stated.")
            move.must_capture = list(cost.undisclosed)
            move.talking_points = self._talking_points(row, best)
            move.warnings.append("Do not compare this against other offers yet.")
            return move

        if analysis["meets_objective"]:
            move.ask = ("Confirm the $0 landed terms in writing, then hand to the human "
                        "for acceptance.")
            move.rationale = (f"Landed cost is {landed_cost.fmt(cost.total_cents)}, "
                              "which meets the campaign objective.")
            move.must_capture = ["Freight terms and who books the carrier",
                                 "Setup/PDI responsibility",
                                 "How long the unit stays and on what terms",
                                 "Whether the unit must be returned, bought, or retailed",
                                 "Insurance and title/registration handling",
                                 "Any purchase or stocking obligation attached"]
            move.warnings.append("The agent does not accept offers. Human signs off.")
            move.talking_points = self._talking_points(row, best)
            return move

        move.ask = self._ladder_ask(rung, cost)
        move.rationale = (
            f"Current landed cost is {landed_cost.fmt(cost.total_cents)} against a "
            f"{landed_cost.fmt(self.settings['campaign']['target_landed_cost_cents'])} "
            f"objective — a gap of {landed_cost.fmt(analysis['gap_cents'])}. "
            + (f"Next unrefused rung: {rung['label']}." if rung
               else "Every ladder rung has been refused."))
        move.must_capture = ["Any revised number, in writing, itemized"]
        move.talking_points = self._talking_points(row, best)
        move.citations = self._citations(conn, int(row["company_id"]))
        move.walk_away = self._walk_away(cost)
        if not rung:
            move.warnings.append(
                "Ladder exhausted. Either accept the documented fallback (human decision) "
                "or close this target and reallocate effort.")
        return move

    # ------------------------------------------------------------- helpers
    def _ladder_ask(self, rung: Mapping[str, Any] | None,
                    cost: landed_cost.LandedCost) -> str:
        if rung is None:
            return ("Nothing left to ask for. Present the documented fallback to the "
                    "human and stop.")
        asks = {
            "free_vehicle": "Ask for the vehicle at no cost as an evaluation unit.",
            "free_freight": "Ask them to cover freight, or to release it at their "
                            "negotiated carrier rate — and confirm whether pickup "
                            "eliminates the charge entirely.",
            "full_first_order_credit": "Ask for 100% of the unit cost credited against "
                                       "a first 5-10 unit order, in writing, with the "
                                       "qualifying order size and expiry stated.",
            "max_discount": "Ask for their best net dealer price on this specific unit.",
            "extended_warranty": "Ask for extended warranty coverage on the unit.",
            "parts_package": "Ask for a parts package to be included.",
            "battery_warranty": "Ask for extended lithium battery warranty coverage.",
            "charger": "Ask for the charger to be included at no cost.",
            "spare_parts": "Ask for a spare/consumable parts kit.",
            "marketing_support": "Ask for display, POS and marketing support.",
        }
        return asks.get(rung["key"], f"Ask for: {rung['label']}")

    def _talking_points(self, row: sqlite3.Row,
                        offer: Mapping[str, Any] | None) -> list[str]:
        points = [
            "A unit on our floor is what converts this territory into orders — "
            "customers buy what they can sit in.",
            "We are asking for one unit, not a container. The risk to them is bounded.",
        ]
        if row["company_type"] == "dealer":
            points.append("An aging demo costs them floor space and floor-plan interest "
                          "every month it sits; we are in a different market and are not "
                          "competing for their retail customers.")
        if row["company_type"] in ("manufacturer", "distributor"):
            points.append("This is territory coverage they do not currently have.")
        points.append("Cosmetic blemishes, freight damage, and non-current model years "
                      "are all acceptable to us — that widens what they can offer.")
        if offer is not None and db.field(offer, "credit_pct"):
            points.append("The credited structure already works for us; the remaining "
                          "question is whether freight is credited too.")
        return points

    def _our_concessions(self) -> list[str]:
        """Only things the profile actually authorizes us to offer."""
        from .profile import BusinessProfile
        try:
            p = BusinessProfile.load()
        except Exception:
            return []
        offers = ["Field evaluation feedback", "Photos and video of the unit in use"]
        if p.get("display_area.type"):
            offers.append(f"Permanent display in our {p.get('display_area.type')}")
        if p.get("marketing_channels"):
            offers.append("Local marketing exposure")
        if p.get("service_capabilities.in_house_service"):
            offers.append("In-market service coverage for the product")
        if p.get("constraints.willing_to_commit_first_order"):
            units = p.get("constraints.first_order_units_willing")
            offers.append(f"A first order commitment"
                          + (f" of {units} units" if units else ""))
        return offers

    def _citations(self, conn: sqlite3.Connection, company_id: int) -> list[dict[str, str]]:
        """Market prices we may cite — only ones with a stored source."""
        citations = []
        for row in db.rows(conn, "SELECT claim, url, quoted_text, retrieved_at FROM sources"
                                 " WHERE claim = 'pricing' ORDER BY retrieved_at DESC"
                                 " LIMIT 5"):
            citations.append({"claim": " ".join((row["quoted_text"] or "").split())[:120],
                              "url": row["url"], "retrieved": row["retrieved_at"]})
        return citations

    def _walk_away(self, cost: landed_cost.LandedCost) -> str | None:
        ceiling = self.settings["campaign"].get("target_landed_cost_cents", 0)
        if cost.total_cents is None:
            return None
        if cost.total_cents > ceiling:
            return (f"Landed cost {landed_cost.fmt(cost.total_cents)} exceeds the "
                    f"{landed_cost.fmt(ceiling)} objective. Continuing past this point "
                    "is a human decision, not an agent one.")
        return None

    def _refused(self, history: Sequence[sqlite3.Row]) -> list[str]:
        refused: list[str] = []
        for row in history:
            for key in db.loads(row["conceded"], []) or []:
                if isinstance(key, str) and not key.startswith("strategy:"):
                    refused.append(key)
        return refused

    def _secured(self, history: Sequence[sqlite3.Row]) -> list[str]:
        secured: list[str] = []
        for row in history:
            ladder = db.loads(row["concession_ladder"], {}) or {}
            secured.extend(ladder.get("secured", []) if isinstance(ladder, dict) else [])
        return secured


def record_round(conn: sqlite3.Connection, target_id: int, move: NegotiationMove, *,
                 their_position: str | None = None, offer_id: int | None = None,
                 refused: Sequence[str] = (), secured: Sequence[str] = ()) -> int:
    """Persist one negotiation round."""
    achieved = None
    if offer_id:
        offer = db.one(conn, "SELECT * FROM offers WHERE id = ?", (offer_id,))
        if offer:
            achieved = landed_cost.compute(offer).total_cents

    negotiation_id = db.insert(conn, "negotiations", {
        "target_id": target_id, "offer_id": offer_id, "round_no": move.round_no,
        "our_position": move.ask, "their_position": their_position,
        "concession_ladder": {"refused": list(refused), "secured": list(secured)},
        "asked_for": [move.ladder_item] if move.ladder_item else [],
        "conceded": list(refused),
        "market_citations": move.citations,
        "target_landed_cost_cents": 0,
        "achieved_landed_cost_cents": achieved,
        "status": "awaiting_them", "next_move": move.ask,
    })
    db.update(conn, "targets", target_id, {"stage": "negotiating"})
    db.audit(conn, action="negotiation_round",
             summary=f"round {move.round_no} on target #{target_id}: {move.ask[:60]}",
             entity_type="negotiation", entity_id=negotiation_id,
             detail={"ladder_item": move.ladder_item, "refused": list(refused),
                     "secured": list(secured)})
    return negotiation_id


def accept_offer(*_args: Any, **_kwargs: Any) -> None:
    raise PermissionError(
        "Offers are accepted by a human, never by this system. Present the landed-cost "
        "breakdown and the terms, then stop.")


def best_offer(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The single best live offer across the campaign, by landed cost."""
    rows = db.rows(conn,
                   "SELECT o.*, c.name AS company FROM offers o"
                   " JOIN targets t ON t.id = o.target_id"
                   " JOIN companies c ON c.id = t.company_id"
                   " WHERE o.status IN ('open','countered','accepted_pending_approval')")
    if not rows:
        return None
    best = min(rows, key=landed_cost.rank_key)
    cost = landed_cost.compute(best)
    return {"offer": dict(best), "cost": cost, "company": best["company"]}
