"""Stage 13 — evidence store and the claim guard.

Two directions of truth are policed here:

  OUTBOUND claims about *us* must trace to a field in business.json. The agent
  may not describe the business as an authorized dealer, cite sales volume, or
  claim licenses/locations/references that the profile does not assert.

  INBOUND claims about *them* must trace to a row in `sources` carrying a URL
  and the exact quoted text. "Dealer", "Distributor", "Wholesale", "Denago
  partner", "Free demo" and "Demo program" are all restricted vocabulary.

The guard is deliberately blunt: it blocks on suspicion and asks a human to
either supply evidence or rewrite. A false block costs a minute; a false claim
costs the relationship.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import db, paths
from .profile import BusinessProfile

# Claims that require a stored `sources` row before they may be asserted
# about a counterparty.
RESTRICTED_CLAIMS: dict[str, tuple[str, ...]] = {
    "dealer": ("dealer_status", "denago_relationship"),
    "distributor": ("company_type",),
    "wholesale": ("wholesale",),
    "denago_partner": ("denago_relationship",),
    "free_demo": ("demo_program",),
    "demo_program": ("demo_program",),
    "excess_inventory": ("excess_inventory",),
    "previous_model_year": ("previous_model_year",),
    "target_model_inventory": ("target_model_inventory",),
    "pricing": ("pricing",),
}

# Language in an outbound draft that asserts something about US.
SELF_CLAIM_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(?:i am|i'm|we are|we're)\s+(?:an?\s+)?(?:authorized\s+)?"
                r"(?:denago\s+)?(?:dealer|distributor|reseller)\b", re.I),
     "dealer_status.denago_authorized_dealer",
     "asserts dealer/distributor status for our business"),
    (re.compile(r"\bauthorized\s+(?:denago\s+)?(?:dealer|distributor|partner)\b", re.I),
     "dealer_status.denago_authorized_dealer",
     "asserts authorized dealer/partner status"),
    (re.compile(r"\bdenago\s+(?:partner|dealer)\b", re.I),
     "dealer_status.denago_authorized_dealer",
     "asserts a Denago relationship"),
    (re.compile(r"\bour\s+dealership\b", re.I),
     "dealer_status.denago_authorized_dealer",
     "refers to our business as a dealership"),
    (re.compile(r"\bwe (?:have )?(?:sold|move|sell)\s+\d+", re.I),
     "_never_satisfiable",
     "asserts historical sales volume, which the profile cannot support — it holds "
     "projections for a new operation, not a sales record"),
    (re.compile(r"\b(\d+)\s*\+?\s*years?\s+(?:in business|of experience|selling)\b", re.I),
     "years_in_business",
     "asserts years in business"),
    (re.compile(r"\bour\s+(?:\d+|[a-z]+)[,\d]*\s*(?:sq\.?\s?ft|square (?:feet|foot))\b", re.I),
     "display_area.square_feet",
     "asserts display/showroom square footage"),
    (re.compile(r"\bwe (?:are )?licensed\b|\bour\s+(?:dealer\s+)?licen[cs]e\b", re.I),
     "licenses.state_dealer_license.held",
     "asserts licensure"),
    (re.compile(r"\bour\s+(?:existing\s+)?locations?\b\s*(?:in|across)\s", re.I),
     "business_address",
     "asserts business locations"),
]

# Negotiation language that would fabricate leverage (Stage 9 rule).
FABRICATED_LEVERAGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:another|a competing|a different)\s+(?:supplier|manufacturer|"
                r"distributor|dealer|vendor)\s+(?:has\s+)?(?:offered|quoted|is offering)",
                re.I),
     "claims a competing offer"),
    (re.compile(r"\bi\s+(?:have|'ve got|already have)\s+(?:another|a better|a competing)"
                r"\s+(?:offer|quote|deal)\b", re.I),
     "claims a competing offer"),
    (re.compile(r"\bi'?m\s+(?:currently\s+)?(?:deciding between|weighing)\b", re.I),
     "implies competing offers"),
    (re.compile(r"\bready to (?:place|sign)\s+(?:the\s+|an?\s+)?(?:order|purchase order|po)\b",
                re.I),
     "implies a commitment that has not been approved"),
]

# Unsourced market-price assertions.
PRICE_CLAIM_PATTERN = re.compile(
    r"\b(?:market|street|retail|msrp|going)\s+(?:price|rate)\b|\$[\d,]{3,}", re.I)


@dataclass
class ClaimViolation:
    kind: str            # self_claim | unevidenced_claim | fabricated_leverage | unsourced_price
    text: str
    reason: str
    remedy: str
    severity: str = "block"   # block | warn

    def render(self) -> str:
        tag = "BLOCK" if self.severity == "block" else "WARN "
        return f"  [{tag}] {self.reason}\n         quote: “{self.text.strip()}”\n         fix:   {self.remedy}"


@dataclass
class GuardResult:
    violations: list[ClaimViolation] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(v.severity == "block" for v in self.violations)

    @property
    def clean(self) -> bool:
        return not self.violations

    def render(self) -> str:
        if not self.violations:
            return "  claim guard: clean — every assertion traces to the profile or a source"
        return "\n".join(v.render() for v in self.violations)


# ------------------------------------------------------------------ sources
def record_source(conn: sqlite3.Connection, *, company_id: int | None, url: str,
                  claim: str, quoted_text: str, title: str | None = None,
                  confidence: str = "medium", local_copy: str | None = None,
                  retrieved_by: str = "agent") -> int:
    """Store one piece of evidence. Required before the matching claim may be used."""
    if not url or not url.strip():
        raise ValueError("evidence requires a source URL")
    if not quoted_text or not quoted_text.strip():
        raise ValueError("evidence requires the exact supporting text — "
                         "a summary is not evidence")
    content_hash = hashlib.sha256(quoted_text.strip().encode("utf-8")).hexdigest()[:32]
    source_id = db.insert(conn, "sources", {
        "company_id": company_id, "url": url.strip(), "title": title,
        "quoted_text": quoted_text.strip(), "claim": claim,
        "retrieved_at": db.utcnow(), "retrieved_by": retrieved_by,
        "content_hash": content_hash, "local_copy_path": local_copy,
        "confidence": confidence,
    })
    db.audit(conn, action="evidence_recorded",
             summary=f"evidence for '{claim}' from {url}",
             entity_type="source", entity_id=source_id,
             detail={"claim": claim, "url": url, "company_id": company_id})
    return source_id


def evidence_for(conn: sqlite3.Connection, company_id: int,
                 claim: str) -> list[sqlite3.Row]:
    return db.rows(conn, "SELECT * FROM sources WHERE company_id = ? AND claim = ?"
                         " ORDER BY retrieved_at DESC", (company_id, claim))


def has_evidence(conn: sqlite3.Connection, company_id: int, claim: str) -> bool:
    return bool(evidence_for(conn, company_id, claim))


def evidence_index(conn: sqlite3.Connection, company_id: int) -> dict[str, list[str]]:
    """claim -> [urls]. Used by the scorer and the claim guard."""
    index: dict[str, list[str]] = {}
    for row in db.rows(conn, "SELECT claim, url FROM sources WHERE company_id = ?",
                       (company_id,)):
        index.setdefault(row["claim"], []).append(row["url"])
    return index


def archive_evidence(text: str, *, label: str) -> Path:
    """Persist a local snapshot of source text so a dead link is not lost."""
    paths.ensure_dirs()
    safe = re.sub(r"[^a-z0-9._-]+", "-", label.lower()).strip("-")[:80]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    path = paths.EVIDENCE_DIR / f"{safe}-{digest}.txt"
    path.write_text(text, encoding="utf-8")
    return path


# -------------------------------------------------------------- claim guard
class ClaimGuard:
    """Scans an outbound draft before it can reach the approval gate."""

    def __init__(self, profile: BusinessProfile,
                 company_evidence: Mapping[str, list[str]] | None = None):
        self.profile = profile
        self.evidence = dict(company_evidence or {})

    def scan(self, text: str, *, asserted_claims: Iterable[str] = (),
             allow_price_claims: bool = False) -> GuardResult:
        result = GuardResult()
        self._scan_self_claims(text, result)
        self._scan_leverage(text, result)
        self._scan_asserted(asserted_claims, result)
        if not allow_price_claims:
            self._scan_prices(text, result)
        return result

    def _scan_self_claims(self, text: str, result: GuardResult) -> None:
        for pattern, profile_path, reason in SELF_CLAIM_PATTERNS:
            for match in pattern.finditer(text):
                if profile_path == "_never_satisfiable":
                    supported = False
                else:
                    value = self.profile.get(profile_path)
                    supported = (bool(value) if isinstance(value, bool)
                                 else value is not None)
                if not supported:
                    remedy = ("There is no truthful source for this — remove it."
                              if profile_path == "_never_satisfiable" else
                              f"business.json → {profile_path} does not support this. "
                              "Either fill that field truthfully or remove the claim.")
                    result.violations.append(ClaimViolation(
                        kind="self_claim", text=match.group(0), reason=reason,
                        remedy=remedy))

    def _scan_leverage(self, text: str, result: GuardResult) -> None:
        for pattern, reason in FABRICATED_LEVERAGE_PATTERNS:
            for match in pattern.finditer(text):
                result.violations.append(ClaimViolation(
                    kind="fabricated_leverage", text=match.group(0), reason=reason,
                    remedy="Remove it unless the offer/commitment genuinely exists and "
                           "is recorded in `offers`. Never invent leverage."))

    def _scan_asserted(self, claims: Iterable[str], result: GuardResult) -> None:
        for claim in claims:
            needed = RESTRICTED_CLAIMS.get(claim, (claim,))
            if not any(self.evidence.get(need) for need in needed):
                result.violations.append(ClaimViolation(
                    kind="unevidenced_claim", text=claim,
                    reason=f"asserts '{claim}' about the counterparty with no stored evidence",
                    remedy=f"Record a source row with claim in {needed} "
                           "(URL + exact quoted text) or drop the assertion."))

    def _scan_prices(self, text: str, result: GuardResult) -> None:
        for match in PRICE_CLAIM_PATTERN.finditer(text):
            if not self.evidence.get("pricing"):
                result.violations.append(ClaimViolation(
                    kind="unsourced_price", text=match.group(0),
                    reason="cites a price or market rate without a stored source",
                    remedy="Record a `pricing` source (URL + quoted listing) and cite it "
                           "inline, or remove the figure.",
                    severity="warn"))
                break
