"""Stage 2 — target discovery and research intake.

This module does not itself browse the web. It owns the research *plan* (which
queries to run, what to collect, what counts as evidence) and the *intake* path
that turns findings into rows — so whatever does the searching, whether Claude
with web search, a human with a browser, or an imported CSV, everything lands in
the same shape with the same evidence requirements.

The intake path refuses a finding that asserts dealer/wholesale/demo status
without a source URL and a quotation. That refusal is the whole point.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import db, evidence, paths, security

CLAIM_REQUIRING_EVIDENCE = {
    "dealer_status": ("evidence_authorized_dealer",),
    "wholesale_status": ("evidence_wholesale",),
    "has_demo_program": (1, True),
    "has_dealer_program": (1, True),
}


@dataclass
class Finding:
    """One researched company, as collected by whatever ran the search."""
    company: str
    website: str | None = None
    location: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    phone: str | None = None
    email: str | None = None
    contact_person: str | None = None
    role: str | None = None
    company_type: str = "unknown"
    products: list[dict[str, Any]] = field(default_factory=list)
    dealer_status: str = "unknown"
    wholesale_status: str = "unknown"
    has_dealer_program: bool = False
    has_demo_program: bool = False
    application_url: str | None = None
    evidence: list[dict[str, str]] = field(default_factory=list)
    source_url: str | None = None
    discovery_query: str | None = None
    estimated_relevance: int | None = None
    notes: str | None = None
    risk_flags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Finding":
        known = {f for f in cls.__dataclass_fields__}          # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        if "zip" in data and "zip_code" not in payload:
            payload["zip_code"] = data["zip"]
        if not payload.get("company"):
            raise ValueError("a finding must name a company")
        return cls(**payload)


@dataclass
class IntakeResult:
    company_id: int
    target_id: int
    created: bool
    evidence_stored: int
    rejected_claims: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def query_plan(settings: Mapping[str, Any] | None = None,
               groups: Sequence[str] | None = None) -> dict[str, Any]:
    """Expand the configured queries, reporting any skipped for missing placeholders."""
    settings = settings or json.loads(paths.SETTINGS.read_text(encoding="utf-8"))
    config = json.loads(paths.SEARCH_QUERIES.read_text(encoding="utf-8"))
    home = settings.get("campaign", {}).get("home_market", {}) or {}

    values = {"city": home.get("city"), "state": home.get("state"),
              "metro": home.get("metro")}
    runnable: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for name, group in config["groups"].items():
        if groups and name not in groups:
            continue
        for query in group["queries"]:
            missing = [key for key in re.findall(r"\{(\w+)\}", query)
                       if not values.get(key)]
            entry = {"group": name, "query": query, "priority": group["priority"]}
            if missing:
                skipped.append({**entry,
                                "reason": f"needs {', '.join(missing)} in "
                                          "config/settings.json campaign.home_market"})
                continue
            expanded = query
            for key, value in values.items():
                if value:
                    expanded = expanded.replace("{" + key + "}", str(value))
            runnable.append({**entry, "query": expanded})

    runnable.sort(key=lambda q: q["priority"])
    return {"runnable": runnable, "skipped": skipped,
            "collect": config["collect_per_target"],
            "evidence_rules": config["evidence_rules"],
            "vetting": config["vetting_checklist"]}


def render_plan(plan: Mapping[str, Any], limit: int | None = None) -> str:
    lines = ["RESEARCH PLAN", "=" * 60, ""]
    current_group = None
    for i, item in enumerate(plan["runnable"]):
        if limit and i >= limit:
            lines.append(f"  ... and {len(plan['runnable']) - limit} more")
            break
        if item["group"] != current_group:
            current_group = item["group"]
            lines.append(f"\n  [{current_group}] (priority {item['priority']})")
        lines.append(f"    - {item['query']}")
    if plan["skipped"]:
        lines += ["", "SKIPPED (missing configuration — not guessed):"]
        lines += [f"    - {s['query']}  <- {s['reason']}" for s in plan["skipped"]]
    lines += ["", "FOR EVERY TARGET, COLLECT:", "  " + ", ".join(plan["collect"])]
    lines += ["", "EVIDENCE IS REQUIRED FOR:",
              "  " + ", ".join(plan["evidence_rules"]["required_for"]),
              "  " + plan["evidence_rules"]["note"]]
    return "\n".join(lines)


def intake(conn: sqlite3.Connection, finding: Finding) -> IntakeResult:
    """Store one research finding, enforcing the evidence rule as it goes."""
    if finding.notes:
        security.assert_storable(finding.notes, context="a research note")

    evidence_claims = {e.get("claim") for e in finding.evidence if e.get("claim")}
    rejected: list[str] = []
    warnings: list[str] = []

    # Strip unevidenced status claims BEFORE they reach the database.
    dealer_status = finding.dealer_status
    if dealer_status == "evidence_authorized_dealer" and \
            "dealer_status" not in evidence_claims and \
            "denago_relationship" not in evidence_claims:
        rejected.append("dealer_status=evidence_authorized_dealer (no evidence supplied)")
        dealer_status = "claims_authorized_dealer" if finding.source_url else "unknown"

    wholesale_status = finding.wholesale_status
    if wholesale_status == "evidence_wholesale" and "wholesale" not in evidence_claims:
        rejected.append("wholesale_status=evidence_wholesale (no evidence supplied)")
        wholesale_status = "claims_wholesale" if finding.source_url else "unknown"

    has_demo = finding.has_demo_program
    if has_demo and "demo_program" not in evidence_claims:
        rejected.append("has_demo_program=true (no evidence supplied)")
        has_demo = False

    has_dealer_program = finding.has_dealer_program
    if has_dealer_program and "dealer_program" not in evidence_claims:
        rejected.append("has_dealer_program=true (no evidence supplied)")
        has_dealer_program = False

    company_id = db.upsert_company(conn, {
        "name": finding.company, "website": finding.website,
        "domain": _domain(finding.website), "company_type": finding.company_type,
        "location": finding.location, "city": finding.city, "state": finding.state,
        "zip": finding.zip_code, "phone": finding.phone, "email": finding.email,
        "dealer_status": dealer_status, "wholesale_status": wholesale_status,
        "has_dealer_program": int(has_dealer_program), "has_demo_program": int(has_demo),
        "application_url": finding.application_url, "notes": finding.notes,
        "risk_flags": finding.risk_flags or None,
    })

    stored = 0
    for item in finding.evidence:
        url = item.get("url") or finding.source_url
        quote = item.get("quoted_text") or item.get("quote")
        claim = item.get("claim")
        if not (url and quote and claim):
            warnings.append(f"evidence item skipped — needs url, claim and quoted_text: "
                            f"{item}")
            continue
        try:
            evidence.record_source(conn, company_id=company_id, url=url, claim=claim,
                                   quoted_text=quote, title=item.get("title"),
                                   confidence=item.get("confidence", "medium"))
            stored += 1
        except ValueError as exc:
            warnings.append(f"evidence rejected: {exc}")

    if finding.contact_person or finding.email or finding.phone:
        _upsert_contact(conn, company_id, finding)

    for product in finding.products:
        _upsert_product(conn, company_id, product)

    existing = db.one(conn, "SELECT id FROM targets WHERE company_id = ?", (company_id,))
    created = existing is None
    if created:
        target_id = db.insert(conn, "targets", {
            "company_id": company_id, "discovery_query": finding.discovery_query,
            "relevance": finding.estimated_relevance, "stage": "discovered",
        })
    else:
        target_id = int(existing["id"])
        if finding.estimated_relevance is not None:
            db.update(conn, "targets", target_id,
                      {"relevance": finding.estimated_relevance})

    if not (finding.email or finding.phone or finding.application_url
            or finding.contact_person):
        warnings.append("No contact channel found — this target cannot be reached yet.")
        db.add_task(conn, title=f"Find a contact for {finding.company}",
                    detail="No email, phone, form or named person recorded.",
                    kind="agent", priority=3, target_id=target_id,
                    blocking_reason="no_contact_channel")

    db.audit(conn, action="target_discovered" if created else "target_updated",
             summary=f"{finding.company} ({finding.company_type}) via "
                     f"{finding.discovery_query or 'manual intake'}",
             entity_type="target", entity_id=target_id,
             detail={"evidence_stored": stored, "rejected_claims": rejected,
                     "source_url": finding.source_url})

    for claim in rejected:
        db.add_task(conn, title=f"Evidence needed: {finding.company} — {claim}",
                    detail="The claim was recorded as unknown because no source URL and "
                           "quotation were supplied. Find the evidence or leave it unknown.",
                    kind="agent", priority=3, target_id=target_id,
                    blocking_reason="missing_evidence")

    return IntakeResult(company_id=company_id, target_id=target_id, created=created,
                        evidence_stored=stored, rejected_claims=rejected,
                        warnings=warnings)


def intake_many(conn: sqlite3.Connection,
                findings: Iterable[Mapping[str, Any]]) -> list[IntakeResult]:
    results = []
    for raw in findings:
        results.append(intake(conn, Finding.from_dict(raw)))
    return results


def _upsert_contact(conn: sqlite3.Connection, company_id: int, finding: Finding) -> None:
    existing = db.one(conn,
                      "SELECT id FROM contacts WHERE company_id = ?"
                      "   AND (email = ? OR (full_name = ? AND full_name IS NOT NULL))",
                      (company_id, finding.email, finding.contact_person))
    payload = {
        "company_id": company_id, "full_name": finding.contact_person,
        "role": finding.role, "email": finding.email, "phone": finding.phone,
        "preferred_channel": "email" if finding.email else
                             ("phone" if finding.phone else "web_form"),
        "is_primary": 1,
    }
    if existing:
        db.update(conn, "contacts", int(existing["id"]),
                  {k: v for k, v in payload.items() if v is not None
                   and k != "company_id"})
    else:
        db.insert(conn, "contacts", payload)


def _upsert_product(conn: sqlite3.Connection, company_id: int,
                    product: Mapping[str, Any]) -> None:
    model = product.get("model")
    existing = db.one(conn, "SELECT id FROM products WHERE company_id = ? AND model = ?",
                      (company_id, model)) if model else None
    payload = {
        "company_id": company_id, "brand": product.get("brand"), "model": model,
        "model_year": product.get("model_year"),
        "passenger_capacity": product.get("passenger_capacity"),
        "powertrain": product.get("powertrain"),
        "condition": product.get("condition", "unknown"),
        "msrp_cents": product.get("msrp_cents"),
        "quoted_price_cents": product.get("quoted_price_cents"),
        "in_stock": product.get("in_stock"),
        "availability_note": product.get("availability_note"),
    }
    if existing:
        db.update(conn, "products", int(existing["id"]),
                  {k: v for k, v in payload.items() if v is not None
                   and k != "company_id"})
    else:
        db.insert(conn, "products", payload)


def _domain(website: str | None) -> str | None:
    if not website:
        return None
    match = re.search(r"https?://([^/]+)", website) or re.match(r"([^/]+)", website)
    return match.group(1).lower().removeprefix("www.") if match else None


def import_json(conn: sqlite3.Connection, path: str) -> list[IntakeResult]:
    """Bulk intake from a JSON file: a list of findings, or {"findings": [...]}"""
    data = json.loads(open(path, encoding="utf-8").read())
    findings = data["findings"] if isinstance(data, dict) else data
    return intake_many(conn, findings)
