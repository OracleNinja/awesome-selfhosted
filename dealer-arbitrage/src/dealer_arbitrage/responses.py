"""Stage 8 — inbound response classification and next-step selection.

Rule-based and transparent on purpose: every classification reports the cues
that produced it, so a human can see why a message was labeled 'offering free
demo' before acting on it. A classifier that cannot explain itself has no place
in a workflow where the output is a commercial decision.

The one hard behavior: a paid offer is never auto-accepted. Detecting money
routes the message to the human, every time.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import db, landed_cost, security

# label -> (weight, [compiled cues]). Highest total weight wins.
CUES: dict[str, tuple[int, tuple[str, ...]]] = {
    "offering_free_demo": (5, (
        r"\b(?:no charge|free of charge|at no cost|won'?t cost you|complimentary)\b",
        r"\bfree (?:demo|unit|cart|sample|evaluation)\b",
        r"\bwe (?:can|could) (?:place|provide|send|ship) (?:you )?(?:a|one) "
        r"(?:demo|evaluation|loaner)\b",
        r"\bdemo(?:nstrator)? (?:unit )?(?:at no cost|free)\b",
    )),
    "offering_credited_demo": (5, (
        r"\bcredit(?:ed)?\b.{0,40}\b(?:first|initial|opening) order\b",
        r"\b(?:first|initial|opening) order\b.{0,40}\bcredit(?:ed)?\b",
        r"\bapply(?:ing)? (?:the )?(?:cost|price|amount).{0,30}\btoward\b",
        r"\brefund(?:ed|able)?\b.{0,30}\b(?:first|initial) (?:order|container)\b",
        r"\bsample (?:fee|cost).{0,30}\b(?:credit|refund)",
    )),
    "offering_paid_demo": (4, (
        r"\bdemo (?:unit )?(?:price|cost|is)\b.{0,20}\$?\d",
        r"\bdealer (?:cost|price|net)\b.{0,20}\$?\d",
        r"\bwe can sell (?:you|it) (?:one|a unit)\b",
        r"\b(?:price|cost) (?:for|of) (?:the|a) demo\b",
    )),
    "offering_discount": (3, (
        r"\b\d{1,2}%\s*(?:off|discount|below)\b",
        r"\bdiscount(?:ed)?\b", r"\bspecial pricing\b", r"\bbest (?:i can do|price)\b",
        r"\bclose ?out\b", r"\bclearance\b",
    )),
    "asking_for_dealer_credentials": (4, (
        r"\bare you (?:an? )?(?:authorized |current )?dealer\b",
        r"\bdealer (?:number|code|account|id)\b",
        r"\bwhich brands do you (?:currently )?(?:carry|sell)\b",
        r"\bwho (?:do you|are you) (?:buy|purchasing) from\b",
        r"\bexisting dealer(?:ship)?\b",
    )),
    "requesting_business_license": (4, (
        r"\bbusiness licen[cs]e\b", r"\bresale certificate\b", r"\bsales tax (?:permit|id)\b",
        r"\btax ?id\b|\bein\b", r"\bw-?9\b", r"\bcertificate of insurance\b",
        r"\bproof of business\b", r"\bdealer licen[cs]e\b",
    )),
    "requesting_initial_order": (4, (
        r"\bminimum (?:order|purchase|quantity)\b", r"\bmoq\b",
        r"\b(?:need|require)(?:s)? (?:an? )?(?:initial|opening|first) order\b",
        r"\bfull container\b", r"\bstocking (?:order|dealer) requirement\b",
        r"\bhow many (?:units )?(?:are you|do you plan to) (?:order|buy)\b",
    )),
    "requesting_information": (2, (
        r"\bcan you (?:tell|send|share|provide)\b", r"\bcould you (?:send|provide)\b",
        r"\bwhat (?:is|are) your\b", r"\bplease (?:complete|fill out|send)\b",
        r"\bfill out (?:the|our) (?:dealer )?application\b",
        r"\bmore (?:information|details) about\b", r"\bwhere are you located\b",
    )),
    "referral": (4, (
        r"\b(?:reach out|contact|speak) (?:to|with) \b.{0,40}\b(?:instead|directly)\b",
        r"\bi'?ll (?:forward|pass) (?:this|you) (?:on|along) to\b",
        r"\bnot (?:my|the right) (?:area|department|person)\b",
        r"\byour (?:regional|territory|area) (?:rep|manager|distributor) is\b",
        r"\bcopying\b.{0,30}\bwho handles\b",
    )),
    "rejection": (5, (
        r"\bnot (?:accepting|taking on|adding) (?:new )?dealers\b",
        r"\bwe (?:don'?t|do not) (?:offer|provide|do) (?:free|demo|loaner)\b",
        r"\bterritory is (?:covered|taken|closed|represented)\b",
        r"\bunable to (?:help|assist|accommodate)\b",
        r"\bnot (?:a fit|interested|something we)\b",
        r"\bwe(?:'| a)re going to pass\b", r"\bmust decline\b",
        r"\bplease (?:remove|take) (?:me|us) (?:off|from)\b",
        r"\bunsubscribe\b|\bdo not (?:contact|email) (?:me|us)\b",
    )),
    "interested": (3, (
        r"\b(?:happy|glad|would be glad) to (?:help|discuss|talk)\b",
        r"\blet'?s (?:set up|schedule|jump on) a (?:call|time)\b",
        r"\bsounds (?:good|great|interesting)\b",
        r"\bwe(?:'| a)re interested\b", r"\bi'?d like to (?:learn|hear) more\b",
        r"\bwhen (?:can|could) (?:you|we) (?:talk|meet|call)\b",
    )),
    "suspicious": (6, (
        r"\bwire transfer only\b", r"\bwestern union\b|\bmoneygram\b",
        r"\b(?:bitcoin|crypto|usdt)\b",
        r"\bdeposit (?:is )?required (?:before|prior to)\b",
        r"\bsend (?:your )?(?:bank|routing|card) (?:details|number)\b",
        r"\bno (?:paperwork|licen[cs]e) (?:needed|required)\b",
    )),
}

COMPILED: dict[str, tuple[int, tuple[re.Pattern[str], ...]]] = {
    label: (weight, tuple(re.compile(p, re.I) for p in patterns))
    for label, (weight, patterns) in CUES.items()
}

MONEY = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?|\b\d{3,6}\s?(?:usd|dollars)\b", re.I)

# What to do next, per classification. Kept next to the classifier so the
# recommendation and the label can never drift apart.
NEXT_STEPS: dict[str, dict[str, str]] = {
    "interested": {
        "action": "Reply with the specific ask and propose a call.",
        "detail": "Restate the evaluation-unit request in one sentence, name the two "
                  "structures that work ($0 unit, or unit cost 100% credited against a "
                  "first 5-10 unit order), and offer two concrete times.",
        "template": "negotiate",
    },
    "requesting_information": {
        "action": "Answer precisely from business.json. Answer nothing that is null.",
        "detail": "Send only fields the profile actually contains. If they asked for "
                  "something missing, flag it for the human rather than estimating.",
        "template": "info_response",
    },
    "asking_for_dealer_credentials": {
        "action": "State the truth: not currently an authorized dealer; applying now.",
        "detail": "Never imply existing dealer status. Position as a prospective dealer "
                  "with a real location, and pivot to what their onboarding requires.",
        "template": "credentials_response",
    },
    "requesting_business_license": {
        "action": "Send only documents flagged approved_for_sharing.",
        "detail": "If a required document is missing from the profile, raise a human "
                  "task. Never send an unapproved or expired document.",
        "template": "documents_response",
    },
    "offering_free_demo": {
        "action": "Confirm terms in writing before celebrating.",
        "detail": "Get in writing: freight (who pays), setup/PDI, any required purchase, "
                  "how long the unit stays, whether it must be returned or bought, "
                  "insurance requirements, and title/registration handling.",
        "template": "confirm_free_demo",
    },
    "offering_credited_demo": {
        "action": "Capture the credit mechanics precisely, then take it to the human.",
        "detail": "Credit percentage, qualifying order size, expiry window, whether "
                  "freight is credited too, and what happens if the first order is "
                  "smaller than projected.",
        "template": "confirm_credited_demo",
    },
    "offering_paid_demo": {
        "action": "DO NOT ACCEPT. Counter up the ladder and route to the human.",
        "detail": "Ask for the unit free or fully credited before discussing price. "
                  "Then get the full landed cost broken out — freight, setup, doc fee, "
                  "taxes, dealer fees, required purchases.",
        "template": "counter_paid_offer",
    },
    "offering_discount": {
        "action": "Counter: ask for full credit against the first order instead.",
        "detail": "A discount is the weakest form of the deal. Trade it for credit, "
                  "freight, or an included parts/charger package.",
        "template": "counter_discount",
    },
    "requesting_initial_order": {
        "action": "Ask what order size unlocks a demo, then take it to the human.",
        "detail": "Do not commit to an order size. Establish the threshold, then let "
                  "the human decide whether it is worth meeting.",
        "template": "order_threshold",
    },
    "rejection": {
        "action": "Ask one question, then close the loop gracefully.",
        "detail": "Ask whether they can refer you to a distributor or dealer who might "
                  "have aging inventory. Mark the target lost and stop follow-ups.",
        "template": "rejection_referral",
    },
    "referral": {
        "action": "Create the referred contact as a new target and thank the referrer.",
        "detail": "A warm referral outranks cold outreach — score the new target and "
                  "draft to them citing the referral by name.",
        "template": "referral_intro",
    },
    "unclear": {
        "action": "Human review — the classifier could not read this confidently.",
        "detail": "Do not draft a reply from a guess about what they meant.",
        "template": None,
    },
    "suspicious": {
        "action": "STOP. Flag the company and take no further action without the human.",
        "detail": "Do not send documents, do not send money, do not continue the thread "
                  "until a human has looked at it.",
        "template": None,
    },
}


@dataclass
class Classification:
    label: str
    confidence: float
    signals: dict[str, list[str]] = field(default_factory=dict)
    secondary: list[str] = field(default_factory=list)
    money_mentioned: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    next_step: dict[str, str] = field(default_factory=dict)
    requires_human: bool = False

    def render(self) -> str:
        lines = [f"  CLASSIFIED: {self.label}  (confidence {self.confidence:.0%})"]
        if self.secondary:
            lines.append(f"  also matched: {', '.join(self.secondary)}")
        for label, cues in self.signals.items():
            lines.append(f"    cues [{label}]: " + "; ".join(f"“{c}”" for c in cues[:3]))
        if self.money_mentioned:
            lines.append(f"    money mentioned: {', '.join(self.money_mentioned)}")
        for flag in self.red_flags:
            lines.append(f"    !! RED FLAG: {flag}")
        if self.next_step:
            lines.append(f"  NEXT: {self.next_step.get('action')}")
            lines.append(f"        {self.next_step.get('detail')}")
        if self.requires_human:
            lines.append("  !! HUMAN DECISION REQUIRED before replying")
        return "\n".join(lines)


def classify(text: str) -> Classification:
    scores: dict[str, int] = {}
    signals: dict[str, list[str]] = {}

    for label, (weight, patterns) in COMPILED.items():
        for pattern in patterns:
            match = pattern.search(text or "")
            if match:
                scores[label] = scores.get(label, 0) + weight
                signals.setdefault(label, []).append(match.group(0).strip())

    money = [m.group(0) for m in MONEY.finditer(text or "")][:5]
    red_flags = [f.reason for f in security.scan_counterparty_text(text or "")]
    if red_flags:
        scores["suspicious"] = scores.get("suspicious", 0) + 6

    # A concrete price alongside demo language means a PAID offer, whatever else the
    # message says — "free demo, the unit is $6,450" is a quote, not a gift. The
    # quoted price outranks promotional language rather than merely competing with
    # it, and the bias is deliberately conservative: mislabeling a real gift as a
    # priced offer costs one human glance, while the reverse could walk a cost into
    # the campaign unnoticed.
    if money and (scores.get("offering_free_demo") or scores.get("offering_discount")):
        promotional = max(scores.get("offering_free_demo", 0),
                          scores.get("offering_discount", 0))
        scores["offering_paid_demo"] = max(scores.get("offering_paid_demo", 0),
                                           promotional + 1)
        signals.setdefault("offering_paid_demo", []).append(f"price quoted: {money[0]}")

    if not scores:
        result = Classification(label="unclear", confidence=0.0,
                                money_mentioned=money, red_flags=red_flags)
        result.next_step = NEXT_STEPS["unclear"]
        result.requires_human = True
        return result

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    label, top = ranked[0]
    total = sum(scores.values())
    confidence = round(top / total, 2) if total else 0.0
    if len(ranked) > 1 and ranked[1][1] == top:
        confidence = min(confidence, 0.5)

    result = Classification(
        label=label, confidence=confidence,
        signals={k: v for k, v in signals.items()},
        secondary=[l for l, _ in ranked[1:4]],
        money_mentioned=money, red_flags=red_flags,
        next_step=NEXT_STEPS.get(label, NEXT_STEPS["unclear"]))
    result.requires_human = (
        label in ("offering_paid_demo", "offering_credited_demo", "suspicious",
                  "requesting_initial_order", "unclear")
        or bool(money) or bool(red_flags) or confidence < 0.4)
    return result


def record(conn: sqlite3.Connection, target_id: int, body: str, *,
           channel: str = "email", outreach_id: int | None = None,
           contact_id: int | None = None,
           received_at: str | None = None) -> tuple[int, Classification]:
    """Store an inbound response, classify it, and queue the next action."""
    security.assert_storable(body, context="an inbound response")
    result = classify(body)

    response_id = db.insert(conn, "responses", {
        "target_id": target_id, "outreach_id": outreach_id, "contact_id": contact_id,
        "channel": channel, "received_at": received_at or db.utcnow(),
        "raw_body": body, "classification": result.label,
        "classification_confidence": result.confidence,
        "classification_signals": result.signals,
        "secondary_labels": result.secondary,
        "suggested_next_step": result.next_step.get("action"),
    })

    stage = {"rejection": "lost", "suspicious": "on_hold"}.get(result.label, "responded")
    updates: dict[str, Any] = {"stage": stage,
                               "owner_next_action": result.next_step.get("action")}
    if result.label == "rejection":
        updates["disqualified_reason"] = "declined by counterparty"
    db.update(conn, "targets", target_id, updates)

    if result.label == "rejection":
        conn.execute("UPDATE followups SET status='cancelled' WHERE target_id = ?"
                     " AND status IN ('scheduled','drafted')", (target_id,))
        if re.search(r"\bunsubscribe\b|\bdo not (?:contact|email)\b|"
                     r"\b(?:remove|take) (?:me|us) (?:off|from)\b", body, re.I):
            conn.execute("UPDATE contacts SET opted_out = 1, opted_out_at = ?"
                         " WHERE company_id = (SELECT company_id FROM targets WHERE id = ?)",
                         (db.utcnow(), target_id))
            db.audit(conn, action="opt_out_recorded",
                     summary="contact opted out — all outreach to this company stops",
                     entity_type="target", entity_id=target_id)

    if result.label == "suspicious" or result.red_flags:
        company_id = db.scalar(conn, "SELECT company_id FROM targets WHERE id = ?",
                               (target_id,), None)
        if company_id:
            security.flag_company(conn, int(company_id),
                                  ["suspicious_response"] +
                                  (["upfront_payment_request"] if any(
                                      "deposit" in f or "payment" in f
                                      for f in result.red_flags) else []),
                                  reason="; ".join(result.red_flags))

    if result.requires_human:
        db.add_task(conn, title=f"Review response: {result.label} (response #{response_id})",
                    detail=result.next_step.get("detail"), kind="human", priority=1,
                    target_id=target_id, entity_type="response", entity_id=response_id,
                    blocking_reason=("money or commitment involved"
                                     if result.money_mentioned else result.label))

    db.audit(conn, action="response_recorded",
             summary=f"response #{response_id} classified {result.label} "
                     f"({result.confidence:.0%})",
             entity_type="response", entity_id=response_id,
             detail={"signals": result.signals, "money": result.money_mentioned,
                     "red_flags": result.red_flags})
    return response_id, result


def offer_from_response(conn: sqlite3.Connection, response_id: int,
                        components: Mapping[str, Any]) -> int:
    """Create an offer row from a classified response. Never auto-accepts it."""
    row = db.one(conn, "SELECT * FROM responses WHERE id = ?", (response_id,))
    if row is None:
        raise ValueError(f"no response with id {response_id}")

    offer_type = {
        "offering_free_demo": "free_unit",
        "offering_credited_demo": "credited_demo",
        "offering_paid_demo": "paid_demo",
        "offering_discount": "discounted_purchase",
    }.get(row["classification"], "other")

    payload: dict[str, Any] = {"target_id": int(row["target_id"]),
                               "response_id": response_id,
                               "offer_type": components.get("offer_type", offer_type),
                               "status": "open"}
    for column, _label in landed_cost.COMPONENTS:
        if column in components:
            payload[column] = components[column]
    for extra in ("product_description", "credit_pct", "credit_conditions",
                  "min_order_units", "includes", "expires_at", "notes",
                  "other_fees_note"):
        if extra in components:
            payload[extra] = components[extra]

    cost = landed_cost.compute(payload)
    payload["landed_cost_cents"] = cost.total_cents
    payload["undisclosed_components"] = cost.undisclosed

    offer_id = db.insert(conn, "offers", payload)
    db.update(conn, "targets", int(row["target_id"]), {"stage": "offer_received"})
    db.audit(conn, action="offer_recorded",
             summary=f"offer #{offer_id} ({payload['offer_type']}) — landed "
                     f"{landed_cost.fmt(cost.total_cents)}",
             entity_type="offer", entity_id=offer_id,
             detail={"undisclosed": cost.undisclosed,
                     "credit_pct": payload.get("credit_pct")})
    if cost.undisclosed:
        db.add_task(conn, title=f"Get undisclosed costs from offer #{offer_id}",
                    detail="Missing: " + ", ".join(cost.undisclosed)
                           + ". An undisclosed component is not $0.",
                    kind="agent", priority=2, target_id=int(row["target_id"]),
                    entity_type="offer", entity_id=offer_id)
    return offer_id
