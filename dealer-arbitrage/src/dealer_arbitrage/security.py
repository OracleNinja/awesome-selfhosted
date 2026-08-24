"""Stage 14 — security boundaries.

The rule behind all of it: this agent operates only where the authenticated
human already has access, and stops the moment a page asks *who are you* or
*will you pay*. It does not solve CAPTCHAs, does not replay credentials, does
not click through identity checks, and does not hold secrets.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from . import db

CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "g-recaptcha", "cf-turnstile",
                   "captcha", "are you a robot", "verify you are human",
                   "cloudflare, please wait")
LOGIN_MARKERS = ("sign in", "log in", "login", "password", "authenticate",
                 "your session has expired", "please log in to continue")
IDENTITY_MARKERS = ("verify your identity", "id.me", "upload your id",
                    "identity verification", "two-factor", "2fa",
                    "one-time code", "verification code")
PAYMENT_MARKERS = ("card number", "credit card", "payment method", "billing information",
                   "cvv", "place order", "complete purchase", "checkout",
                   "authorize payment", "bank account", "routing number")
SIGNATURE_MARKERS = ("docusign", "hellosign", "e-signature", "electronically sign",
                     "i agree to the terms", "adobe sign")

# Text patterns that indicate a counterparty is fishing for money or credentials.
COUNTERPARTY_RED_FLAGS: tuple[tuple[str, str], ...] = (
    (r"wire transfer only|only accept wire|western union|moneygram|zelle only|"
     r"cash ?app|crypto|bitcoin|usdt",
     "demands an irreversible payment method"),
    (r"deposit (?:is )?required (?:before|prior to)|pay (?:a )?deposit to (?:see|view|hold)",
     "wants a deposit before any verification"),
    (r"send (?:your |the )?(?:bank|account|routing|card) (?:details|information|number)",
     "requests financial account details"),
    (r"(?:send|provide) (?:your |the )?(?:ssn|social security)",
     "requests a Social Security number"),
    (r"act (?:now|fast)|today only|limited time|price expires (?:today|in \d+ hours)",
     "manufactured urgency"),
    (r"no (?:paperwork|license|documentation) (?:needed|required)",
     "offers to skip dealer documentation — a legitimate wholesaler does not"),
    (r"(?:i am|i'm) (?:currently )?(?:travel|abroad|overseas).{0,40}(?:agent|shipper)",
     "classic escrow/shipping-agent fraud pattern"),
)

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "payment card number"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*\S+", re.I), "password"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}"), "bearer token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API secret key"),
)


class HumanActionRequired(RuntimeError):
    """Raised when the agent hits a wall it must not climb."""


@dataclass
class RedFlag:
    pattern: str
    reason: str
    excerpt: str


def detect_wall(page_text: str) -> str | None:
    """Identify an authentication/verification/payment wall in page content."""
    haystack = (page_text or "").lower()
    for marker in CAPTCHA_MARKERS:
        if marker in haystack:
            return "CAPTCHA present — a human must solve it. The agent will not."
    for marker in IDENTITY_MARKERS:
        if marker in haystack:
            return f"Identity verification required ('{marker}') — human action needed."
    for marker in PAYMENT_MARKERS:
        if marker in haystack:
            return (f"Payment step detected ('{marker}') — the agent never authorizes "
                    "payment.")
    for marker in SIGNATURE_MARKERS:
        if marker in haystack:
            return (f"E-signature / terms acceptance detected ('{marker}') — a human "
                    "must read and sign.")
    for marker in LOGIN_MARKERS:
        if marker in haystack:
            return (f"Login required ('{marker}') — the agent works only inside an "
                    "already-authenticated session.")
    return None


def assert_no_auth_wall(page_text: str, url: str = "") -> None:
    wall = detect_wall(page_text)
    if wall:
        raise HumanActionRequired(f"{wall}{f' [{url}]' if url else ''}")


def scan_counterparty_text(text: str) -> list[RedFlag]:
    """Look for fraud and pressure markers in an inbound message or listing."""
    flags: list[RedFlag] = []
    for pattern, reason in COUNTERPARTY_RED_FLAGS:
        match = re.search(pattern, text or "", re.I)
        if match:
            start = max(match.start() - 40, 0)
            flags.append(RedFlag(pattern=pattern, reason=reason,
                                 excerpt=text[start:match.end() + 40].strip()))
    return flags


def scan_for_secrets(text: str) -> list[str]:
    """Refuse to persist anything that looks like a credential or card number."""
    found = []
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(text or ""):
            found.append(label)
    return found


def assert_storable(text: str, *, context: str = "text") -> None:
    secrets = scan_for_secrets(text)
    if secrets:
        raise HumanActionRequired(
            f"Refusing to store {context}: it contains {', '.join(secrets)}. "
            "Redact it first — this system never holds credentials or payment data.")


def domain_allowed(url: str, settings: Mapping[str, Any]) -> tuple[bool, str]:
    """Browser automation is opt-in per domain."""
    browser = settings.get("security", {}).get("browser_automation", {})
    if not browser.get("enabled", False):
        return False, "browser automation is disabled in config/settings.json"
    allowed = browser.get("allowed_domains") or []
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False, f"cannot parse a hostname from {url!r}"
    if not allowed:
        return False, (f"'{host}' is not in security.browser_automation.allowed_domains "
                       "(the list is empty) — ask the human before automating a new site")
    for entry in allowed:
        entry = entry.lower().lstrip("*.")
        if host == entry or host.endswith("." + entry):
            return True, f"'{host}' is allowlisted"
    return False, f"'{host}' is not in the browser automation allowlist"


def pause_for_human(conn: sqlite3.Connection, reason: str, *,
                    target_id: int | None = None, url: str | None = None,
                    entity_type: str | None = None,
                    entity_id: int | None = None) -> int:
    """Stop, record why, and put it in front of the human."""
    task_id = db.add_task(conn, title=f"Human action required: {reason}",
                          detail=f"url: {url}" if url else None,
                          kind="blocked", priority=1, target_id=target_id,
                          blocking_reason=reason, entity_type=entity_type,
                          entity_id=entity_id)
    db.audit(conn, action="paused_for_human", summary=reason,
             entity_type=entity_type, entity_id=entity_id,
             detail={"url": url, "task_id": task_id})
    return task_id


def flag_company(conn: sqlite3.Connection, company_id: int,
                 flags: Sequence[str], *, reason: str = "") -> None:
    """Attach risk flags to a company; the scorer penalizes them."""
    row = db.one(conn, "SELECT risk_flags, name FROM companies WHERE id = ?", (company_id,))
    if row is None:
        return
    current = set(db.loads(row["risk_flags"], []) or [])
    current.update(flags)
    db.update(conn, "companies", company_id, {"risk_flags": sorted(current)})
    db.audit(conn, action="company_flagged",
             summary=f"{row['name']} flagged: {', '.join(flags)}",
             entity_type="company", entity_id=company_id,
             detail={"reason": reason, "flags": sorted(current)})
