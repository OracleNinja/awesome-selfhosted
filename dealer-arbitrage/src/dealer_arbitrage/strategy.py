"""Stage 4 — acquisition strategy selection.

Picks the highest-ranked strategy whose preconditions the target actually
satisfies, using the signals that fired during scoring. Two hard rules:

  * `retail_purchase` is never selected automatically. It is listed as the
    documented fallback and requires a human to choose it.
  * Any strategy that commits money is marked requires_human_approval and
    carries the reason, so the approval gate can show what is being committed.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import db, paths
from .scoring import ScoreResult


@dataclass
class StrategyChoice:
    key: str
    rank: int
    label: str
    ask: str
    rationale: str
    requires_human_approval: bool
    approval_reason: str | None
    target_landed_cost_cents: int | None
    what_we_offer: list[str] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    must_capture: list[str] = field(default_factory=list)
    note: str | None = None
    blocked: bool = False
    blocked_reason: str | None = None

    def render(self) -> str:
        lines = [f"  STRATEGY  #{self.rank}  {self.label}",
                 f"    ask:       {self.ask}",
                 f"    why:       {self.rationale}"]
        if self.what_we_offer:
            lines.append("    we offer:  " + "; ".join(self.what_we_offer))
        if self.fallbacks:
            lines.append("    fallback:  " + " -> ".join(self.fallbacks))
        if self.must_capture:
            lines.append("    capture:   " + "; ".join(self.must_capture))
        if self.requires_human_approval:
            lines.append(f"    APPROVAL:  required — {self.approval_reason}")
        if self.blocked:
            lines.append(f"    BLOCKED:   {self.blocked_reason}")
        if self.note:
            lines.append(f"    note:      {self.note}")
        return "\n".join(lines)


class StrategyEngine:
    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = config or json.loads(paths.STRATEGIES.read_text(encoding="utf-8"))
        self.strategies = sorted(self.config["strategies"], key=lambda s: s["rank"])
        self.never_default = set(self.config.get("never_default_to", []))

    def applicable(self, company_type: str, fired_signals: Sequence[str]
                   ) -> list[dict[str, Any]]:
        fired = set(fired_signals)
        out = []
        for spec in self.strategies:
            when = spec.get("applies_when", {})
            if spec["key"] in self.never_default:
                continue
            if when.get("always"):
                out.append(spec)
                continue
            types = when.get("company_type_in")
            if types and company_type not in types:
                continue
            needed = when.get("any_signal")
            if needed and not (fired & set(needed)):
                continue
            out.append(spec)
        return out

    def choose(self, company_type: str, fired_signals: Sequence[str],
               *, exhausted: Sequence[str] = ()) -> StrategyChoice:
        exhausted_set = set(exhausted)
        candidates = [s for s in self.applicable(company_type, fired_signals)
                      if s["key"] not in exhausted_set]
        fired = set(fired_signals)

        if not candidates:
            retail = next(s for s in self.strategies if s["key"] == "retail_purchase")
            return StrategyChoice(
                key=retail["key"], rank=retail["rank"], label=retail["label"],
                ask=retail.get("ask", "Purchase at the best negotiated price."),
                rationale=("No sponsored, credited or discounted channel is supported by "
                           "this target's evidence."),
                requires_human_approval=True,
                approval_reason=retail.get("approval_reason"),
                target_landed_cost_cents=None,
                blocked=True,
                blocked_reason=retail.get("blocked_until",
                                          "Requires an explicit human decision."),
                note=retail.get("note"))

        best = candidates[0]
        fallbacks = [s["label"] for s in candidates[1:4]]
        matched = sorted(fired & set(best.get("applies_when", {}).get("any_signal", [])))
        rationale = (f"company_type={company_type}"
                     + (f"; signals: {', '.join(matched)}" if matched else
                        "; no signal precondition")
                     + (f"; {len(exhausted_set)} strategy(ies) already exhausted"
                        if exhausted_set else ""))
        return StrategyChoice(
            key=best["key"], rank=best["rank"], label=best["label"],
            ask=best.get("ask", ""), rationale=rationale,
            requires_human_approval=bool(best.get("requires_human_approval")),
            approval_reason=best.get("approval_reason"),
            target_landed_cost_cents=best.get("target_landed_cost_cents"),
            what_we_offer=best.get("what_we_offer", []),
            fallbacks=fallbacks,
            must_capture=best.get("must_capture", []),
            note=best.get("note"))

    # ------------------------------------------------------ concession ladder
    def ladder(self) -> list[dict[str, Any]]:
        return list(self.config["concession_ladder"]["items"])

    def ladder_rules(self) -> list[str]:
        return list(self.config["concession_ladder"].get("rules", []))

    def next_concession(self, refused: Sequence[str],
                        secured: Sequence[str] = ()) -> dict[str, Any] | None:
        """The highest ladder rung not yet refused or already secured."""
        spent = set(refused) | set(secured)
        for item in self.ladder():
            if item["key"] not in spent:
                return item
        return None


def assign_strategy(conn: sqlite3.Connection, target_id: int,
                    score_result: ScoreResult | None = None,
                    engine: StrategyEngine | None = None) -> StrategyChoice:
    """Select and persist the strategy for one target."""
    engine = engine or StrategyEngine()
    row = db.one(conn, "SELECT t.*, c.company_type, c.name FROM targets t"
                       " JOIN companies c ON c.id = t.company_id WHERE t.id = ?",
                 (target_id,))
    if row is None:
        raise ValueError(f"no target with id {target_id}")

    if score_result is not None:
        fired = [h.key for h in score_result.positives]
    else:
        breakdown = db.loads(row["score_breakdown"], {}) or {}
        fired = [p["key"] for p in breakdown.get("positives", [])]

    exhausted_keys = _exhausted_strategies(conn, target_id)
    choice = engine.choose(row["company_type"], fired, exhausted=exhausted_keys)

    db.update(conn, "targets", target_id, {
        "strategy": choice.key, "strategy_rank": choice.rank,
        "strategy_rationale": choice.rationale,
    })
    db.audit(conn, action="strategy_assigned",
             summary=f"{row['name']}: {choice.label}",
             entity_type="target", entity_id=target_id,
             detail={"key": choice.key, "rank": choice.rank,
                     "rationale": choice.rationale, "fired_signals": fired,
                     "exhausted": exhausted_keys})
    return choice


def _exhausted_strategies(conn: sqlite3.Connection, target_id: int) -> list[str]:
    """Strategies already refused for this target, read from negotiation history."""
    exhausted: list[str] = []
    for row in db.rows(conn, "SELECT conceded, their_position FROM negotiations"
                             " WHERE target_id = ?", (target_id,)):
        for key in db.loads(row["conceded"], []) or []:
            if isinstance(key, str) and key.startswith("strategy:"):
                exhausted.append(key.split(":", 1)[1])
    return exhausted
