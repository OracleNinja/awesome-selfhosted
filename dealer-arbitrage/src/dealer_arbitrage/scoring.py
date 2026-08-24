"""Stage 3 — the 0-100 target scoring model.

Config-driven (config/scoring.json) so weights can be tuned without code
changes. The rule that gives the score meaning: a positive signal marked
`requires_evidence` contributes NOTHING until a matching row exists in
`sources`. An unevidenced hunch scores zero, by construction.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import db, evidence, paths


@dataclass
class SignalHit:
    key: str
    label: str
    weight: int
    evidenced: bool
    evidence_urls: list[str] = field(default_factory=list)
    detail: str | None = None


@dataclass
class ScoreResult:
    target_id: int | None
    company: str
    score: int
    tier: str
    positives: list[SignalHit] = field(default_factory=list)
    penalties: list[SignalHit] = field(default_factory=list)
    suppressed: list[SignalHit] = field(default_factory=list)   # matched but unevidenced
    p_demo: int = 0
    p_free_unit: int = 0
    p_credited_unit: int = 0
    rationale: str = ""
    disqualify: bool = False
    flags: list[str] = field(default_factory=list)

    def breakdown(self) -> dict[str, Any]:
        return {
            "positives": [{"key": h.key, "weight": h.weight,
                           "evidence": h.evidence_urls} for h in self.positives],
            "penalties": [{"key": h.key, "weight": h.weight,
                           "detail": h.detail} for h in self.penalties],
            "suppressed_unevidenced": [h.key for h in self.suppressed],
            "probabilities": {"p_demo": self.p_demo, "p_free_unit": self.p_free_unit,
                              "p_credited_unit": self.p_credited_unit},
        }

    def render(self) -> str:
        lines = [f"  {self.company}  —  SCORE {self.score}/100   [{self.tier}]"]
        for hit in self.positives:
            src = f"  <- {hit.evidence_urls[0]}" if hit.evidence_urls else ""
            lines.append(f"    +{hit.weight:<3} {hit.label}{src}")
        for hit in self.penalties:
            lines.append(f"    {hit.weight:<4} {hit.label}"
                         + (f" ({hit.detail})" if hit.detail else ""))
        for hit in self.suppressed:
            lines.append(f"    +0   {hit.label}  [SUPPRESSED: no evidence stored]")
        lines.append(f"    probabilities: demo {self.p_demo}%  free {self.p_free_unit}%"
                     f"  credited {self.p_credited_unit}%")
        if self.disqualify:
            lines.append("    !! AUTO-DISQUALIFY triggered")
        for flag in self.flags:
            lines.append(f"    !! {flag}")
        return "\n".join(lines)


class Scorer:
    def __init__(self, config: Mapping[str, Any] | None = None,
                 settings: Mapping[str, Any] | None = None):
        self.config = config or json.loads(paths.SCORING.read_text(encoding="utf-8"))
        self.settings = settings or json.loads(paths.SETTINGS.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ context
    def build_context(self, conn: sqlite3.Connection, company_id: int) -> dict[str, Any]:
        company = db.one(conn, "SELECT * FROM companies WHERE id = ?", (company_id,))
        if company is None:
            raise ValueError(f"no company with id {company_id}")
        target = db.one(conn, "SELECT * FROM targets WHERE company_id = ?", (company_id,))
        products = db.rows(conn, "SELECT * FROM products WHERE company_id = ?", (company_id,))
        contacts = db.rows(conn, "SELECT * FROM contacts WHERE company_id = ?", (company_id,))
        sources = db.rows(conn, "SELECT * FROM sources WHERE company_id = ?", (company_id,))
        response_count = db.scalar(
            conn, "SELECT COUNT(*) FROM responses r JOIN targets t ON t.id = r.target_id"
                  " WHERE t.company_id = ?", (company_id,))

        corpus_parts = [company["name"] or "", company["notes"] or ""]
        corpus_parts += [s["quoted_text"] or "" for s in sources]
        corpus_parts += [f"{p['brand'] or ''} {p['model'] or ''} {p['condition'] or ''}"
                         f" {p['availability_note'] or ''}" for p in products]
        corpus = " \n".join(corpus_parts).lower()

        home = (self.settings.get("campaign", {}).get("home_market") or {})
        metro_cities = [c.lower() for c in (home.get("metro_cities") or [])]
        company_city = (company["city"] or "").lower()

        distance = None
        if company_city and company_city in metro_cities:
            distance = 40          # inside the declared metro — pickup is realistic
        elif company["state"] and home.get("state") and company["state"] == home["state"]:
            distance = 250         # same state — freight is cheap but not trivial

        return {
            "company": company, "target": target, "products": products,
            "contacts": contacts, "sources": sources, "corpus": corpus,
            "response_count": int(response_count or 0),
            "distance_miles": distance,
            "evidence": evidence.evidence_index(conn, company_id),
            "risk_flags": db.loads(company["risk_flags"], []) or [],
            "opted_out": any(c["opted_out"] for c in contacts),
            "freight_estimate_cents": (target["freight_estimate_cents"] if target else None),
        }

    # -------------------------------------------------------------- score
    def score(self, conn: sqlite3.Connection, company_id: int) -> ScoreResult:
        ctx = self.build_context(conn, company_id)
        company = ctx["company"]
        result = ScoreResult(
            target_id=int(ctx["target"]["id"]) if ctx["target"] else None,
            company=company["name"], score=0, tier="")

        total = int(self.config.get("base_score", 0))

        for key, spec in self.config["positive_signals"].items():
            matched, detail = self._detect(spec.get("detect", {}), ctx)
            if not matched:
                continue
            urls: list[str] = []
            if spec.get("requires_evidence"):
                claim = spec.get("evidence_claim", key)
                urls = ctx["evidence"].get(claim, [])
                if not urls:
                    result.suppressed.append(SignalHit(
                        key=key, label=spec["label"], weight=0, evidenced=False,
                        detail="requires evidence claim: " + claim))
                    continue
            hit = SignalHit(key=key, label=spec["label"], weight=int(spec["weight"]),
                            evidenced=True, evidence_urls=urls, detail=detail)
            result.positives.append(hit)
            total += hit.weight

        for key, spec in self.config["penalties"].items():
            matched, detail = self._detect(spec.get("detect", {}), ctx)
            if not matched:
                continue
            weight = int(spec["weight"])
            if spec.get("scaled") and key == "high_freight_cost":
                weight = self._scale_freight_penalty(ctx, spec)
            result.penalties.append(SignalHit(key=key, label=spec["label"], weight=weight,
                                              evidenced=True, detail=detail))
            total += weight
            if spec.get("auto_disqualify"):
                result.disqualify = True
            if spec.get("auto_flag"):
                result.flags.append(spec["auto_flag"])

        clamp = self.config.get("clamp", {"min": 0, "max": 100})
        result.score = max(clamp["min"], min(clamp["max"], total))
        result.tier = self._tier(result.score)

        fired = {h.key for h in result.positives}
        probs = self.config["probability_model"]
        result.p_demo = self._probability(probs["p_demo"], fired)
        result.p_free_unit = self._probability(probs["p_free_unit"], fired)
        result.p_credited_unit = self._probability(probs["p_credited_unit"], fired)
        result.rationale = self._rationale(result)
        return result

    def _scale_freight_penalty(self, ctx: Mapping[str, Any],
                               spec: Mapping[str, Any]) -> int:
        ceiling = int(spec["detect"].get("freight_estimate_cents_gt", 0))
        actual = ctx.get("freight_estimate_cents") or 0
        over = max(actual - ceiling, 0)
        extra = (over // 50000) * 4          # -4 per additional $500 over the ceiling
        return max(int(spec["weight"]) - extra, -20)

    def _tier(self, score: int) -> str:
        for tier in self.config.get("tiers", []):
            if score >= tier["min"]:
                return tier["label"]
        return "unranked"

    def _probability(self, spec: Mapping[str, Any], fired: set[str]) -> int:
        value = int(spec.get("base", 0))
        for key, add in spec.get("add", {}).items():
            if key in fired:
                value += int(add)
        return max(0, min(int(spec.get("cap", 100)), value))

    def _rationale(self, result: ScoreResult) -> str:
        if not result.positives and not result.penalties:
            return "No scoring signals fired — target needs research before it can be ranked."
        pos = ", ".join(h.key for h in result.positives) or "none"
        neg = ", ".join(h.key for h in result.penalties) or "none"
        note = ""
        if result.suppressed:
            note = (f" Suppressed for lack of evidence: "
                    f"{', '.join(h.key for h in result.suppressed)}.")
        return f"Positive: {pos}. Penalties: {neg}.{note}"

    # ------------------------------------------------------------ detection
    def _detect(self, clause: Mapping[str, Any],
                ctx: Mapping[str, Any]) -> tuple[bool, str | None]:
        """Evaluate one detect clause. All present keys must match (AND)."""
        if not clause:
            return False, None
        company = ctx["company"]
        matched_detail: list[str] = []

        for key, expected in clause.items():
            if key == "any_text":
                hits = [t for t in expected if t.lower() in ctx["corpus"]]
                if not hits:
                    return False, None
                matched_detail.append("matched: " + ", ".join(hits[:3]))
            elif key == "in":
                continue                                     # descriptive only
            elif key == "company_type_in":
                if company["company_type"] not in expected:
                    return False, None
            elif key == "state_in":
                if (company["state"] or "").upper() not in [s.upper() for s in expected]:
                    return False, None
            elif key == "wholesale_status_in":
                if company["wholesale_status"] not in expected:
                    return False, None
            elif key == "distance_miles_lte":
                distance = ctx.get("distance_miles")
                if distance is None or distance > expected:
                    return False, None
                matched_detail.append(f"~{distance} mi")
            elif key == "has_named_contact_with_channel":
                ok = any(c["full_name"] and (c["email"] or c["phone"])
                         for c in ctx["contacts"])
                if ok != bool(expected):
                    return False, None
            elif key == "response_count_gte":
                if ctx["response_count"] < int(expected):
                    return False, None
            elif key == "has_no_contact_channel":
                any_channel = bool(company["email"] or company["phone"]
                                   or company["application_url"]
                                   or any(c["email"] or c["phone"] for c in ctx["contacts"]))
                if any_channel == bool(expected):
                    return False, None
            elif key == "no_program_signals":
                # Evidenced wholesale access counts as a program: a wholesaler that
                # sells at dealer pricing is a channel, whether or not it publishes
                # a formal "dealer program" page.
                has_program = bool(company["has_dealer_program"]
                                   or company["has_demo_program"]
                                   or company["wholesale_status"] == "evidence_wholesale")
                if has_program == bool(expected):
                    return False, None
            elif key == "no_relevant_products":
                relevant = [p for p in ctx["products"]
                            if (p["powertrain"] or "").lower().find("lith") >= 0
                            or (p["passenger_capacity"] or 0) >= 6
                            or (p["brand"] or "").lower() in ("denago", "tao", "tao motor")]
                if bool(relevant) == bool(expected):
                    return False, None
            elif key == "freight_estimate_cents_gt":
                freight = ctx.get("freight_estimate_cents")
                if freight is None or freight <= int(expected):
                    return False, None
                matched_detail.append(f"freight est. ${freight / 100:,.0f}")
            elif key == "identity_verified":
                if int(company["identity_verified"]) != int(expected):
                    return False, None
            elif key == "and_any":
                if not any(flag in ctx["risk_flags"] for flag in expected):
                    return False, None
            elif key == "risk_flags_any":
                hits = [f for f in expected if f in ctx["risk_flags"]]
                if not hits:
                    return False, None
                matched_detail.append("flags: " + ", ".join(hits))
            elif key == "opted_out":
                if bool(ctx["opted_out"]) != bool(expected):
                    return False, None
            else:
                return False, None                            # unknown clause: never fires
        return True, ("; ".join(matched_detail) or None)


def score_and_store(conn: sqlite3.Connection, company_id: int,
                    scorer: Scorer | None = None) -> ScoreResult:
    """Score a target and persist the result plus its breakdown."""
    scorer = scorer or Scorer()
    result = scorer.score(conn, company_id)
    target = db.one(conn, "SELECT * FROM targets WHERE company_id = ?", (company_id,))
    if target is None:
        target_id = db.insert(conn, "targets", {"company_id": company_id})
    else:
        target_id = int(target["id"])
    result.target_id = target_id

    values: dict[str, Any] = {
        "score": result.score, "score_breakdown": result.breakdown(),
        "scored_at": db.utcnow(), "p_demo": result.p_demo,
        "p_free_unit": result.p_free_unit, "p_credited_unit": result.p_credited_unit,
        "prob_rationale": result.rationale,
    }
    if result.disqualify:
        values["stage"] = "disqualified"
        values["disqualified_reason"] = "auto-disqualified by scoring: " + result.rationale
    elif target is not None and target["stage"] == "discovered":
        values["stage"] = "researched"
    db.update(conn, "targets", target_id, values)
    db.audit(conn, action="target_scored",
             summary=f"{result.company} scored {result.score}/100",
             entity_type="target", entity_id=target_id, detail=result.breakdown())
    for hit in result.suppressed:
        spec = scorer.config["positive_signals"].get(hit.key, {})
        if int(spec.get("weight", 0)) >= 10:
            db.add_task(
                conn,
                title=f"Find evidence: {result.company} — {hit.label}",
                detail=(f"Signal '{hit.key}' matched but scored 0 because no source row "
                        f"exists for claim '{spec.get('evidence_claim', hit.key)}'. "
                        f"Worth +{spec.get('weight')} once evidenced. Record it with "
                        "`da evidence add --company-id ... --claim ... --url ... --quote ...`"),
                kind="agent", priority=2, target_id=target_id,
                blocking_reason="missing_evidence")

    if result.flags:
        for flag in result.flags:
            db.add_task(conn, title=f"Review {result.company}: {flag}",
                        detail=result.rationale, kind="human", priority=1,
                        target_id=target_id, blocking_reason=flag)
    return result


def score_all(conn: sqlite3.Connection) -> list[ScoreResult]:
    scorer = Scorer()
    results = [score_and_store(conn, int(row["id"]), scorer)
               for row in db.rows(conn, "SELECT id FROM companies ORDER BY id")]
    return sorted(results, key=lambda r: r.score, reverse=True)
