"""Stage 5 — personalized outreach generation.

Three properties this module is built to guarantee:

  1. No generic spam. A draft will not render unless it references at least
     `requires_personalization` specific, evidenced facts about that particular
     company. A target with no research produces a blocked draft and a research
     task, not a mail-merge.
  2. No invented facts about us. Every substitution comes from business.json.
     A missing field leaves a visible [MISSING: field] marker and blocks the
     draft rather than smoothing over the gap with a plausible sentence.
  3. No unevidenced claims about them. Every draft passes through the Stage 13
     claim guard before it can reach the approval gate.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import db, evidence, landed_cost, paths
from .evidence import ClaimGuard, GuardResult
from .profile import BusinessProfile

PLACEHOLDER = re.compile(r"\{\{([a-z_]+)(?:\|([^}]*))?\}\}")
MISSING = "[MISSING: {field}]"

AUDIENCE_TEMPLATES = {
    "manufacturer": "manufacturer.md",
    "distributor": "distributor.md",
    "dealer": "dealer.md",
    "wholesaler": "wholesaler.md",
    "broker": "wholesaler.md",
    "liquidator": "wholesaler.md",
    "marketplace": "wholesaler.md",
}


@dataclass
class Template:
    key: str
    path: Path
    meta: dict[str, Any]
    body: str

    @property
    def subject(self) -> str:
        return self.meta.get("subject", "")

    @property
    def required_personalization(self) -> int:
        return int(self.meta.get("requires_personalization", 1))

    @property
    def profile_level(self) -> str:
        return self.meta.get("requires_profile_level", "outreach")


@dataclass
class Draft:
    target_id: int
    company: str
    channel: str
    contact_id: int | None
    contact_label: str
    template_key: str
    angle: str | None
    subject: str
    body: str
    ask: str
    expected_outcome: str
    risks: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    personalization: list[dict[str, str]] = field(default_factory=list)
    claims_asserted: list[str] = field(default_factory=list)
    cost_commitment_cents: int = 0
    missing_fields: list[str] = field(default_factory=list)
    unresolved_placeholders: list[str] = field(default_factory=list)
    guard: GuardResult | None = None
    blocked_reasons: list[str] = field(default_factory=list)
    outreach_id: int | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reasons) or bool(self.guard and self.guard.blocked)

    def render_preview(self) -> str:
        head = [f"  TO:      {self.contact_label} @ {self.company}",
                f"  CHANNEL: {self.channel}",
                f"  SUBJECT: {self.subject}", ""]
        return "\n".join(head) + self.body


def load_template(name: str) -> Template:
    path = paths.TEMPLATE_DIR / "outreach" / name
    if not path.exists():
        path = paths.TEMPLATE_DIR / "followup" / name
    if not path.exists():
        raise FileNotFoundError(f"template not found: {name}")
    meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    return Template(key=meta.get("key", path.stem), path=path, meta=meta, body=body)


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal front-matter reader — scalars, inline lists, one nesting level.

    Deliberately not a YAML dependency: the templates are ours, the grammar is
    fixed, and a parser we control cannot surprise us on someone else's input.
    """
    if not text.startswith("---"):
        return {}, text
    _, raw, body = text.split("---", 2)
    meta: dict[str, Any] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        indented = line.startswith("  ")
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if indented and current_key:
            meta.setdefault(current_key, {})
            if isinstance(meta[current_key], dict):
                meta[current_key][key.strip()] = _coerce(value)
            continue
        current_key = key.strip()
        meta[current_key] = _coerce(value) if value else {}
    return meta, body.lstrip("\n")


def _coerce(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [v.strip().strip("\"'") for v in inner.split(",") if v.strip()]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.strip('"').strip("'").lstrip("-").isdigit():
        return int(value.strip('"').strip("'"))
    return value.strip('"').strip("'")


# ------------------------------------------------------------ personalization
def gather_personalization(conn: sqlite3.Connection, company_id: int,
                           limit: int = 3) -> list[dict[str, str]]:
    """Specific, evidenced facts about this company that a message can cite.

    Sourced only from stored evidence rows and products, so a referenced fact is
    always something we can point at a URL for if challenged.
    """
    facts: list[dict[str, str]] = []
    for row in db.rows(conn,
                       "SELECT claim, quoted_text, url, title FROM sources"
                       " WHERE company_id = ? ORDER BY"
                       "   CASE claim"
                       "     WHEN 'demo_program' THEN 1 WHEN 'dealer_program' THEN 2"
                       "     WHEN 'target_model_inventory' THEN 3"
                       "     WHEN 'excess_inventory' THEN 4"
                       "     WHEN 'previous_model_year' THEN 5"
                       "     WHEN 'denago_relationship' THEN 6 ELSE 9 END,"
                       "   retrieved_at DESC", (company_id,)):
        quote = " ".join((row["quoted_text"] or "").split())
        if len(quote) > 220:
            quote = quote[:217] + "..."
        facts.append({"claim": row["claim"], "quote": quote, "url": row["url"],
                      "title": row["title"] or ""})
        if len(facts) >= limit:
            break
    return facts


def compose_personalization_paragraph(facts: Sequence[Mapping[str, str]],
                                      company: str) -> str:
    """Turn evidenced facts into an opening line that proves we did the reading."""
    if not facts:
        return ""
    lead = facts[0]
    phrasing = {
        "demo_program": "I saw that {company} runs a demo/evaluation unit program",
        "dealer_program": "I came across {company}'s dealer program page",
        "target_model_inventory": "I noticed {company} carries the model I'm after",
        "excess_inventory": "I saw {company} has clearance/overstock inventory listed",
        "previous_model_year": "I noticed {company} still has non-current model-year units",
        "denago_relationship": "I saw that {company} carries Denago",
        "wholesale": "I saw that {company} sells wholesale/at dealer pricing",
        "company_type": "I found {company} while researching manufacturers and distributors",
    }.get(lead["claim"], "I came across {company} while researching this market")
    opener = phrasing.format(company=company)
    return f"{opener} — “{lead['quote']}” ({lead['url']})."


# -------------------------------------------------------------- draft build
class OutreachBuilder:
    def __init__(self, profile: BusinessProfile, settings: Mapping[str, Any] | None = None):
        self.profile = profile
        self.settings = settings or json.loads(paths.SETTINGS.read_text(encoding="utf-8"))

    def build(self, conn: sqlite3.Connection, target_id: int, *,
              channel: str = "email", template_name: str | None = None,
              contact_id: int | None = None, angle: str | None = None,
              sequence_no: int = 1, extra_context: Mapping[str, Any] | None = None
              ) -> Draft:
        row = db.one(conn,
                     "SELECT t.*, c.name AS company_name, c.company_type, c.city AS c_city,"
                     "       c.state AS c_state, c.email AS c_email, c.phone AS c_phone,"
                     "       c.id AS company_id, c.application_url"
                     " FROM targets t JOIN companies c ON c.id = t.company_id"
                     " WHERE t.id = ?", (target_id,))
        if row is None:
            raise ValueError(f"no target with id {target_id}")

        company_id = int(row["company_id"])
        template = load_template(template_name
                                 or AUDIENCE_TEMPLATES.get(row["company_type"],
                                                           "wholesaler.md"))
        facts = gather_personalization(conn, company_id)
        contact = self._pick_contact(conn, company_id, contact_id)

        draft = Draft(
            target_id=target_id, company=row["company_name"], channel=channel,
            contact_id=int(contact["id"]) if contact else None,
            contact_label=self._contact_label(contact, row),
            template_key=template.key, angle=angle or template.meta.get("angle"),
            subject="", body="", ask="", expected_outcome="",
            personalization=[dict(f) for f in facts],
        )

        # --- gates -------------------------------------------------------
        report = self.profile.validate(template.profile_level)
        if not report.ok:
            draft.missing_fields = report.missing_required
            draft.blocked_reasons.append(
                "business.json is missing required fields: "
                + ", ".join(report.missing_required))
        for violation in report.security_violations:
            draft.blocked_reasons.append(f"profile security violation: {violation}")

        if len(facts) < template.required_personalization:
            draft.blocked_reasons.append(
                f"only {len(facts)} evidenced fact(s) about {row['company_name']}; "
                f"this template requires {template.required_personalization}. "
                "Research the target first — do not send a generic message.")
            db.add_task(conn, title=f"Research {row['company_name']} before outreach",
                        detail="Outreach blocked: insufficient evidenced personalization.",
                        kind="agent", priority=2, target_id=target_id,
                        blocking_reason="insufficient_personalization")

        opted_out = db.scalar(conn, "SELECT COUNT(*) FROM contacts WHERE company_id = ?"
                                    " AND opted_out = 1", (company_id,), 0)
        if opted_out:
            draft.blocked_reasons.append(
                f"{opted_out} contact(s) at this company have opted out — the whole "
                "company is closed to further outreach, general inbox included")

        touches = db.scalar(conn, "SELECT COUNT(*) FROM outreach WHERE target_id = ?"
                                  " AND status = 'sent'", (target_id,))
        max_touches = self.settings["autonomy"].get("max_touches_per_company", 5)
        if touches >= max_touches:
            draft.blocked_reasons.append(
                f"already sent {touches} messages to this company "
                f"(limit {max_touches}) — stop rather than pester")

        # --- render ------------------------------------------------------
        context = self._context(row, contact, facts, template, extra_context or {})
        draft.subject = self._substitute(template.subject, context, draft).strip()
        draft.body = self._substitute(template.body, context, draft)
        draft.ask = context.get("ask_primary", "")
        draft.expected_outcome = self._expected_outcome(row, template)
        draft.risks = self._risks(row, contact, facts)
        draft.claims_asserted = sorted({f["claim"] for f in facts})
        draft.cost_commitment_cents = 0     # first-touch outreach commits nothing

        guard = ClaimGuard(self.profile, evidence.evidence_index(conn, company_id))
        draft.guard = guard.scan(draft.body + "\n" + draft.subject,
                                 asserted_claims=draft.claims_asserted)
        if draft.guard.blocked:
            draft.blocked_reasons.append("claim guard blocked the draft (see violations)")
        return draft

    # ------------------------------------------------------------- helpers
    def _pick_contact(self, conn: sqlite3.Connection, company_id: int,
                      contact_id: int | None) -> sqlite3.Row | None:
        if contact_id:
            return db.one(conn, "SELECT * FROM contacts WHERE id = ?", (contact_id,))
        return db.one(conn,
                      "SELECT * FROM contacts WHERE company_id = ? AND opted_out = 0"
                      " ORDER BY is_primary DESC,"
                      "   CASE WHEN email IS NOT NULL THEN 0 ELSE 1 END, id",
                      (company_id,))

    def _contact_label(self, contact: sqlite3.Row | None, row: sqlite3.Row) -> str:
        if contact:
            name = contact["full_name"] or "(unnamed contact)"
            role = f", {contact['role']}" if contact["role"] else ""
            where = contact["email"] or contact["phone"] or "no direct channel"
            return f"{name}{role} <{where}>"
        if row["c_email"]:
            return f"(general inbox) <{row['c_email']}>"
        if row["application_url"]:
            return f"(web form) <{row['application_url']}>"
        return "(NO CONTACT IDENTIFIED — research required)"

    def _context(self, row: sqlite3.Row, contact: sqlite3.Row | None,
                 facts: Sequence[Mapping[str, str]], template: Template,
                 extra: Mapping[str, Any]) -> dict[str, Any]:
        p = self.profile.facts_for_outreach()
        asks = template.meta.get("ask_variants", {}) or {}
        first_name = ""
        if contact and contact["full_name"]:
            head = str(contact["full_name"]).split()[0]
            # "A. Rep" -> greeting someone as "A." reads worse than the template's
            # neutral fallback, so an initial does not count as a first name.
            first_name = head if len(head.rstrip(".")) > 1 else ""


        ctx: dict[str, Any] = {
            "owner_name": p["owner_name"],
            "business_name": p["business_name"],
            "legal_name": p["legal_name"],
            "phone": p["phone"],
            "email": p["email"],
            "city": p["city"],
            "state": p["state"],
            "market": p["market"] or (f"{p['city']}, {p['state']}"
                                      if p["city"] and p["state"] else None),
            "business_description": p["business_description"],
            "company": row["company_name"],
            "contact_first_name": first_name or None,
            "their_brand": self._their_brand(facts),
            "original_subject": extra.get("original_subject"),
            "sent_date": extra.get("sent_date"),
            "ask_primary": asks.get("primary"),
            "ask_secondary": asks.get("secondary"),
            "personalization": compose_personalization_paragraph(facts,
                                                                 row["company_name"]),
            "website_line": f"\n{p['website']}" if p["website"] else "",
            "storefront_line": (f"We operate {p['storefront']}." if p["storefront"] else ""),
            "service_line": ("We do service work in-house."
                             if p["service"] else ""),
            "receiving_line": self._receiving_line(p),
            "plan_line": self._plan_line(p),
            "offer_lines": self._offer_lines(row),
            "distance_line": (f"about {row['c_city']}"
                              if row["c_city"] and row["c_city"] != p["city"] else None),
        }
        ctx.update(extra)
        return ctx

    def _their_brand(self, facts: Sequence[Mapping[str, str]]) -> str | None:
        for fact in facts:
            if fact["claim"] == "denago_relationship":
                return "Denago"
        return None

    def _receiving_line(self, p: Mapping[str, Any]) -> str:
        if not p["receiving"]:
            return ""
        extra = " We have a forklift on site." if p["forklift"] else ""
        return f"For delivery: {p['receiving']}.{extra}"

    def _plan_line(self, p: Mapping[str, Any]) -> str:
        """Projections, always phrased as plans — never as achieved history."""
        parts = []
        if p["projected_initial_inventory"]:
            parts.append(f"My plan is {p['projected_initial_inventory']}")
        if p["projected_annual_volume"]:
            parts.append(f"building toward {p['projected_annual_volume']}")
        if not parts:
            return ""
        return (" ".join(parts).rstrip(".")
                + ". Those are projections for a new operation, not past sales.")

    def _offer_lines(self, row: sqlite3.Row) -> str:
        """What we give back — only things the profile actually supports."""
        p = self.profile.facts_for_outreach()
        items = ["Real-world evaluation feedback you can use with other dealers",
                 "Photos and video of the unit in the field"]
        if p["display_area"]:
            items.append(f"Permanent display in our {p['display_area']}")
        if p["marketing_channels"]:
            items.append("Local marketing through "
                         + ", ".join(str(c) for c in p["marketing_channels"][:3]))
        if p["service"]:
            items.append("In-house service coverage for the product in this market")
        return "What I can offer in return:\n" + "\n".join(f"- {i}" for i in items)

    def _expected_outcome(self, row: sqlite3.Row, template: Template) -> str:
        strategy = row["strategy"] or "unassigned strategy"
        return (f"A reply that either opens a dealer file or names the terms under which "
                f"an evaluation unit is available. Pursuing: {strategy}. "
                f"Best case: $0 landed evaluation unit. Realistic case: dealer packet "
                f"plus terms for a credited demo.")

    def _risks(self, row: sqlite3.Row, contact: sqlite3.Row | None,
               facts: Sequence[Mapping[str, str]]) -> list[str]:
        risks: list[str] = []
        if not contact:
            risks.append("No named contact — message goes to a general inbox, "
                         "lower response rate and less accountability.")
        if len(facts) < 2:
            risks.append("Thin personalization — reads closer to a template than a letter.")
        if row["company_type"] == "dealer":
            risks.append("A dealer may read this as a competitor scouting their inventory; "
                         "the territory distinction has to stay explicit.")
        if row["company_type"] == "manufacturer":
            risks.append("Asking a manufacturer for a free unit as an unestablished dealer "
                         "can end the conversation; the credited fallback must be in the "
                         "same message.")
        risks.append("Reputational: this is a real business introduction under the owner's "
                     "name. Anything inaccurate in it is a lasting problem.")
        return risks

    def _substitute(self, text: str, context: Mapping[str, Any], draft: Draft) -> str:
        def replace(match: re.Match[str]) -> str:
            key, fallback = match.group(1), match.group(2)
            value = context.get(key)
            if value not in (None, ""):
                return str(value)
            if fallback:
                return fallback
            draft.unresolved_placeholders.append(key)
            return MISSING.format(field=key)

        rendered = PLACEHOLDER.sub(replace, text)
        rendered = re.sub(r"[ \t]+\n", "\n", rendered)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        return rendered.strip() + "\n"


# ------------------------------------------------------------------ storage
def save_draft(conn: sqlite3.Connection, draft: Draft, *,
               sequence_no: int = 1) -> int:
    """Persist a draft as status='draft'. Never 'sent' — only approval.py does that."""
    paths.ensure_dirs()
    safe = re.sub(r"[^a-z0-9]+", "-", draft.company.lower()).strip("-")[:50]
    draft_path = paths.DRAFT_DIR / f"{safe}-{draft.template_key}-seq{sequence_no}.md"
    draft_path.write_text(draft.render_preview(), encoding="utf-8")

    outreach_id = db.insert(conn, "outreach", {
        "target_id": draft.target_id, "contact_id": draft.contact_id,
        "channel": draft.channel, "direction": "outbound", "sequence_no": sequence_no,
        "template_key": draft.template_key, "angle": draft.angle,
        "subject": draft.subject, "body": draft.body,
        "attachments": draft.attachments,
        "personalization": draft.personalization,
        "claims_asserted": draft.claims_asserted,
        "ask": draft.ask, "expected_outcome": draft.expected_outcome,
        "risks": draft.risks,
        "cost_commitment_cents": draft.cost_commitment_cents,
        "status": "draft", "draft_path": str(draft_path),
    })
    draft.outreach_id = outreach_id
    db.update(conn, "targets", draft.target_id, {"stage": "outreach_drafted"})
    db.audit(conn, action="outreach_drafted",
             summary=f"draft #{outreach_id} for {draft.company} ({draft.channel})",
             entity_type="outreach", entity_id=outreach_id,
             detail={"blocked": draft.blocked, "reasons": draft.blocked_reasons,
                     "personalization_count": len(draft.personalization),
                     "draft_path": str(draft_path)})
    return outreach_id
