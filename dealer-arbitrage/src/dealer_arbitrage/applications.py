"""Stage 6 — dealer/wholesale application preparation.

The agent opens the application, inspects the form, maps business.json onto it,
fills what it can prove, flags what it cannot, screenshots the filled state, and
STOPS. It does not submit. `submit()` raises, unconditionally.

Browser automation is pluggable: Playwright when installed, otherwise a manual
adapter that emits a precise operator script for a human or for Claude driving
Chrome. Both paths produce the identical READY FOR APPROVAL report, so the
review a human performs does not depend on which one ran.
"""
from __future__ import annotations

import functools
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from . import db, paths, security
from .profile import BusinessProfile

# Form-field keyword -> profile dotted path, in PRIORITY order: the first entry
# whose keyword appears in the field's label/name wins. Order matters — "email"
# precedes "address" so "Email Address" maps to the email, not the street. Matching
# is word-bounded, so "city" cannot match inside "capacity".
FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("legal business name", "legal_name"),
    ("legal name", "legal_name"),
    ("company name", "business_name"),
    ("business name", "business_name"),
    ("dba", "business_name"),
    ("owner name", "owner_name"),
    ("owner", "owner_name"),
    ("principal", "owner_name"),
    ("contact name", "owner_name"),
    ("first name", "owner_name"),
    ("last name", "owner_name"),
    ("full name", "owner_name"),
    ("email", "email"),
    ("phone", "phone"),
    ("mobile", "phone"),
    ("website", "website"),
    ("url", "website"),
    ("facebook", "social_profiles.facebook"),
    ("instagram", "social_profiles.instagram"),
    ("street", "business_address"),
    ("address line 1", "business_address"),
    ("address 1", "business_address"),
    ("address line 2", "address_line_2"),
    ("address 2", "address_line_2"),
    ("address", "business_address"),
    ("city", "city"),
    ("state", "state"),
    ("province", "state"),
    ("zip", "zip"),
    ("postal", "zip"),
    ("county", "county"),
    ("country", "_country_us"),
    ("territory", "territory.primary_market"),
    ("market area", "territory.primary_market"),
    ("years in business", "years_in_business"),
    ("year established", "business_start_date"),
    ("business type", "tax_information.entity_type"),
    ("entity type", "tax_information.entity_type"),
    ("ein", "tax_information.ein"),
    ("federal tax id", "tax_information.ein"),
    ("tax id", "tax_information.ein"),
    ("resale", "tax_information.resale_certificate_on_file"),
    ("sales tax permit", "licenses.sales_tax_permit.number"),
    ("sales tax", "licenses.sales_tax_permit.number"),
    ("business license", "licenses.business_license.number"),
    ("dealer license", "licenses.state_dealer_license.number"),
    ("insurance", "licenses.liability_insurance.carrier"),
    ("current brands", "dealer_status.other_authorized_brands"),
    ("brands carried", "dealer_status.other_authorized_brands"),
    ("current products", "current_products"),
    ("products", "current_products"),
    ("annual volume", "projected_annual_volume.units"),
    ("annual sales", "projected_annual_volume.units"),
    ("units per year", "projected_annual_volume.units"),
    ("initial order", "projected_initial_inventory.units"),
    ("opening order", "projected_initial_inventory.units"),
    ("showroom", "display_area.square_feet"),
    ("display area", "display_area.square_feet"),
    ("square", "display_area.square_feet"),
    ("service", "service_capabilities.in_house_service"),
    ("technician", "service_capabilities.technicians"),
    ("shipping", "shipping_capability.receiving_method"),
    ("receiving", "shipping_capability.receiving_method"),
    ("forklift", "shipping_capability.forklift"),
    ("loading dock", "shipping_capability.loading_dock"),
    ("how did you hear", "_how_heard"),
    ("comments", "business_description"),
    ("tell us", "business_description"),
    ("describe", "business_description"),
    ("message", "business_description"),
)

# Field names that must never be auto-filled, whatever the mapping says.
NEVER_AUTOFILL = re.compile(
    r"password|ssn|social.?security|credit.?card|card.?number|cvv|routing|"
    r"account.?number|signature|initial(s)?\b|date.?signed|agree|accept.?terms|"
    r"captcha|guarantee|guarantor|date.?of.?birth|\bdob\b|driver.?licen[cs]e",
    re.I)

# Fields asking for an identifier rather than a yes/no answer.
WANTS_IDENTIFIER = re.compile(r"\bnumber\b|\bno\.\b|\bid\b|\b#\b|\bpermit\b|"
                              r"\blicen[cs]e\b|\bcertificate\b", re.I)

# Fields where a wrong answer is a misrepresentation — human confirms every time.
ALWAYS_CONFIRM = re.compile(
    r"authorized|dealer.?status|existing.?dealer|volume|revenue|sales.?history|"
    r"net.?worth|financial|credit|bank|reference|years.?in.?business|"
    r"number.?of.?(employees|locations|stores)", re.I)


@functools.lru_cache(maxsize=512)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(keyword) + r"\b")


def _keyword_matches(keyword: str, haystack: str) -> bool:
    return bool(_keyword_pattern(keyword).search(haystack))


@dataclass
class FormField:
    name: str
    label: str = ""
    field_type: str = "text"
    required: bool = False
    options: list[str] = field(default_factory=list)
    selector: str = ""

    @staticmethod
    def _normalize(text: str) -> str:
        return (text or "").lower().replace("_", " ").replace("-", " ")

    @property
    def label_text(self) -> str:
        return self._normalize(self.label)

    @property
    def name_text(self) -> str:
        return self._normalize(self.name)

    @property
    def haystack(self) -> str:
        """Combined text, used for the never-autofill and always-confirm screens."""
        return f"{self.label_text} {self.name_text}".strip()


@dataclass
class MappedField:
    field: FormField
    profile_path: str | None
    value: Any = None
    action: str = "fill"          # fill | human_confirm | human_supply | skip | blocked
    reason: str = ""

    @property
    def display_value(self) -> str:
        if self.value is None:
            return "(blank)"
        if isinstance(self.value, (list, dict)):
            return json.dumps(self.value)
        return str(self.value)


@dataclass
class ApplicationPlan:
    target_id: int
    company: str
    url: str
    program_name: str
    fields: list[MappedField] = field(default_factory=list)
    human_required: list[str] = field(default_factory=list)
    attachments: list[int] = field(default_factory=list)
    withheld_attachments: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    application_id: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def filled(self) -> dict[str, str]:
        return {m.field.name: m.display_value for m in self.fields if m.action == "fill"}

    @property
    def unfilled(self) -> dict[str, str]:
        return {m.field.name: m.reason for m in self.fields
                if m.action in ("human_supply", "human_confirm", "blocked")}

    @property
    def ready(self) -> bool:
        return not any(m.action == "blocked" for m in self.fields)

    def render_report(self) -> str:
        bar = "=" * 78
        lines = [bar, "READY FOR APPROVAL — DEALER APPLICATION", bar,
                 f"COMPANY:      {self.company}",
                 f"PROGRAM:      {self.program_name}",
                 f"URL:          {self.url}",
                 f"PREPARED:     {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", ""]

        lines.append("FIELDS THE AGENT FILLED (all values come from business.json):")
        filled = [m for m in self.fields if m.action == "fill"]
        if filled:
            for m in filled:
                lines.append(f"  {m.field.label or m.field.name:<34} {m.display_value}")
                origin = (f"business.json: {m.profile_path}" if m.profile_path
                          else "static value (not from the profile)")
                lines.append(f"  {'':<34} <- {origin}")
        else:
            lines.append("  (none)")

        confirm = [m for m in self.fields if m.action == "human_confirm"]
        if confirm:
            lines += ["", "FIELDS REQUIRING YOUR CONFIRMATION BEFORE SUBMISSION:"]
            for m in confirm:
                lines.append(f"  {m.field.label or m.field.name:<34} "
                             f"{m.display_value}   [{m.reason}]")

        supply = [m for m in self.fields if m.action == "human_supply"]
        if supply:
            lines += ["", "FIELDS ONLY YOU CAN SUPPLY (left blank — never guessed):"]
            for m in supply:
                lines.append(f"  {m.field.label or m.field.name:<34} {m.reason}")

        blocked = [m for m in self.fields if m.action == "blocked"]
        if blocked:
            lines += ["", "BLOCKED FIELDS (agent will not touch these):"]
            for m in blocked:
                lines.append(f"  {m.field.label or m.field.name:<34} {m.reason}")

        lines += ["", "ATTACHMENTS:"]
        lines += [f"  #{doc}" for doc in self.attachments] or ["  (none)"]
        if self.withheld_attachments:
            lines += ["  WITHHELD (not approved for sharing):"]
            lines += [f"    {label}" for label in self.withheld_attachments]

        if self.human_required:
            lines += ["", "REQUIRES A HUMAN AT THE KEYBOARD:"]
            lines += [f"  !! {item}" for item in self.human_required]

        if self.notes:
            lines += ["", "NOTES:"]
            lines += [f"  - {note}" for note in self.notes]

        lines += ["", f"SCREENSHOT:   {self.screenshot_path or '(not captured)'}",
                  "", bar,
                  "The agent has STOPPED. It will not submit this application.",
                  "Reply APPROVE SEND to authorize submission, EDIT to revise, "
                  "or CANCEL to drop it.",
                  bar]
        return "\n".join(lines)


# ---------------------------------------------------------------- adapters
class BrowserAdapter(Protocol):
    def inspect(self, url: str) -> list[FormField]: ...
    def fill(self, url: str, values: Mapping[str, str]) -> str | None: ...


class ManualAdapter:
    """No browser available: emit an operator script instead of pretending.

    Used when Playwright is absent, and when a human (or Claude driving Chrome)
    is doing the clicking. It never invents form structure — an un-inspected form
    returns nothing and the caller must supply the field list.
    """

    name = "manual"

    def inspect(self, url: str) -> list[FormField]:
        return []

    def fill(self, url: str, values: Mapping[str, str]) -> str | None:
        script = [f"# Operator script — {url}", "",
                  "1. Open the URL in the authenticated browser session.",
                  "2. Enter these values exactly as written:", ""]
        script += [f"   {name}: {value}" for name, value in values.items()]
        script += ["", "3. Do NOT submit. Capture a full-page screenshot.",
                   "4. Save the screenshot under var/screenshots/ and register it with",
                   "   `da application screenshot <id> <path>`.", ""]
        path = paths.EXPORT_DIR / "operator-script.txt"
        paths.ensure_dirs()
        path.write_text("\n".join(script), encoding="utf-8")
        return None


class PlaywrightAdapter:
    """Real browser automation, when Playwright is installed.

    Fills fields and screenshots. There is no submit method — not disabled, not
    guarded by a flag: absent, so no code path can reach one.
    """

    name = "playwright"

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    @staticmethod
    def available() -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def _page(self, browser_ctx: Any, url: str) -> Any:
        page = browser_ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        page.goto(url, wait_until="domcontentloaded")
        return page

    def inspect(self, url: str) -> list[FormField]:
        from playwright.sync_api import sync_playwright

        fields: list[FormField] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            try:
                page = self._page(browser.new_context(), url)
                security.assert_no_auth_wall(page.content(), url)
                for element in page.query_selector_all("input, select, textarea"):
                    input_type = (element.get_attribute("type") or "text").lower()
                    if input_type in ("hidden", "submit", "button", "image", "reset"):
                        continue
                    name = (element.get_attribute("name")
                            or element.get_attribute("id") or "")
                    if not name:
                        continue
                    label = (element.get_attribute("aria-label")
                             or element.get_attribute("placeholder") or "")
                    if not label:
                        label = page.evaluate(
                            "el => { const id = el.id;"
                            " const l = id && document.querySelector(`label[for='${id}']`);"
                            " return l ? l.innerText.trim() : ''; }", element) or ""
                    options = [o.inner_text().strip()
                               for o in element.query_selector_all("option")] \
                        if element.evaluate("el => el.tagName.toLowerCase()") == "select" else []
                    fields.append(FormField(
                        name=name, label=label, field_type=input_type,
                        required=element.get_attribute("required") is not None,
                        options=options, selector=f"[name='{name}']" if name else ""))
            finally:
                browser.close()
        return fields

    def fill(self, url: str, values: Mapping[str, str]) -> str | None:
        from playwright.sync_api import sync_playwright

        paths.ensure_dirs()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        shot = paths.SCREENSHOT_DIR / f"application-{stamp}.png"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            try:
                page = self._page(browser.new_context(), url)
                security.assert_no_auth_wall(page.content(), url)
                for name, value in values.items():
                    try:
                        page.fill(f"[name='{name}']", str(value))
                    except Exception:                     # field moved or is a select
                        try:
                            page.select_option(f"[name='{name}']", str(value))
                        except Exception:
                            continue
                page.screenshot(path=str(shot), full_page=True)
            finally:
                browser.close()
        return str(shot)


def get_adapter(prefer_browser: bool = True) -> BrowserAdapter:
    if prefer_browser and PlaywrightAdapter.available():
        return PlaywrightAdapter()
    return ManualAdapter()


# ------------------------------------------------------------ field mapping
def map_field(form_field: FormField, profile: BusinessProfile) -> MappedField:
    haystack = form_field.haystack

    if NEVER_AUTOFILL.search(haystack):
        return MappedField(form_field, None, action="blocked",
                           reason="credential, signature, identity or payment field — "
                                  "the agent never fills these")

    profile_path = None
    for candidate in (form_field.label_text, form_field.name_text):
        if not candidate:
            continue
        for keyword, path in FIELD_MAP:
            if _keyword_matches(keyword, candidate):
                profile_path = path
                break
        if profile_path:
            break

    if profile_path is None:
        return MappedField(form_field, None, action="human_supply",
                           reason="no confident mapping to business.json — "
                                  "the agent will not guess")

    if profile_path == "_country_us":
        return MappedField(form_field, None, value="United States", action="fill",
                           reason="static")
    if profile_path == "_how_heard":
        return MappedField(form_field, None, value="Researching manufacturers and "
                                                   "distributors for a new dealership",
                           action="fill", reason="static, accurate")

    if profile.is_sensitive(profile_path):
        value = profile.get(profile_path)
        if value is None:
            return MappedField(form_field, profile_path, action="human_supply",
                               reason=f"sensitive value not in profile/sensitive.local.json "
                                      f"({profile_path})")
        return MappedField(form_field, profile_path, value=value, action="human_confirm",
                           reason="sensitive identifier — confirm before it is transmitted")

    value = profile.get(profile_path)
    if value is None:
        return MappedField(form_field, profile_path, action="human_supply",
                           reason=f"business.json → {profile_path} is null; "
                                  "the agent will not invent it")

    if ALWAYS_CONFIRM.search(haystack):
        return MappedField(form_field, profile_path, value=value, action="human_confirm",
                           reason="a wrong answer here is a misrepresentation")

    if isinstance(value, bool) and WANTS_IDENTIFIER.search(haystack):
        return MappedField(form_field, profile_path, action="human_supply",
                           reason=f"asks for an identifier but business.json → "
                                  f"{profile_path} holds a yes/no — mapping is unreliable, "
                                  "so the agent leaves it blank")
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    return MappedField(form_field, profile_path, value=value, action="fill")


def prepare(conn: sqlite3.Connection, target_id: int, url: str, *,
            profile: BusinessProfile, form_fields: Sequence[FormField] | None = None,
            program_name: str = "Dealer application",
            adapter: BrowserAdapter | None = None,
            attachments: Sequence[int] = ()) -> ApplicationPlan:
    """Inspect, map, fill and screenshot an application — then stop."""
    row = db.one(conn, "SELECT t.id, c.name AS company FROM targets t"
                       " JOIN companies c ON c.id = t.company_id WHERE t.id = ?",
                 (target_id,))
    if row is None:
        raise ValueError(f"no target with id {target_id}")

    adapter = adapter or get_adapter()
    plan = ApplicationPlan(target_id=target_id, company=row["company"], url=url,
                           program_name=program_name)

    fields = list(form_fields or [])
    if not fields:
        try:
            fields = adapter.inspect(url)
        except security.HumanActionRequired as exc:
            plan.human_required.append(str(exc))
            plan.notes.append("Form inspection stopped at an authentication or "
                              "verification wall.")
        except Exception as exc:                       # network, timeout, bad selector
            plan.notes.append(f"Automated inspection failed ({exc.__class__.__name__}: "
                              f"{exc}). Supply the field list manually or use the "
                              "operator script.")
    if not fields:
        plan.human_required.append(
            "Form could not be inspected automatically — a human (or Claude in Chrome) "
            "must open the page and list the fields.")

    plan.fields = [map_field(f, profile) for f in fields]

    for mapped in plan.fields:
        if mapped.action == "blocked" and NEVER_AUTOFILL.search(mapped.field.haystack):
            plan.human_required.append(
                f"'{mapped.field.label or mapped.field.name}' must be completed by you")

    # Attachments: only documents explicitly approved for sharing.
    for doc_id in attachments:
        doc = db.one(conn, "SELECT * FROM documents WHERE id = ?", (doc_id,))
        if doc is None:
            plan.notes.append(f"document #{doc_id} not found — skipped")
        elif not doc["approved_for_sharing"]:
            plan.withheld_attachments.append(
                f"{doc['label']} (not approved for sharing)")
        else:
            plan.attachments.append(int(doc_id))

    if adapter.__class__ is not ManualAdapter and plan.filled:
        try:
            plan.screenshot_path = adapter.fill(url, plan.filled)
        except security.HumanActionRequired as exc:
            plan.human_required.append(str(exc))
        except Exception as exc:
            plan.notes.append(f"Automated fill failed ({exc.__class__.__name__}: {exc}).")
    elif plan.filled:
        adapter.fill(url, plan.filled)
        plan.notes.append("No browser automation available — an operator script was "
                          "written to var/exports/operator-script.txt.")

    payload = {
        "target_id": target_id, "program_name": program_name, "url": url,
        "method": "web_form",
        "field_map": {m.field.name: m.profile_path for m in plan.fields if m.profile_path},
        "filled_fields": plan.filled, "unfilled_fields": plan.unfilled,
        "human_required": plan.human_required, "attachments": plan.attachments,
        "status": "needs_human" if plan.human_required else "draft",
    }
    # An un-submitted application for the same form is revised in place, so
    # re-running `prepare` refreshes the draft instead of queueing a duplicate.
    existing = db.one(conn,
                      "SELECT id FROM applications WHERE target_id = ? AND url = ?"
                      "   AND status IN ('draft','needs_human','ready_for_approval')"
                      " ORDER BY id DESC LIMIT 1", (target_id, url))
    if existing:
        application_id = int(existing["id"])
        db.update(conn, "applications", application_id, payload)
    else:
        application_id = db.insert(conn, "applications", payload)
    plan.application_id = application_id

    report_path = paths.EXPORT_DIR / f"application-{application_id}-ready.txt"
    paths.ensure_dirs()
    report_path.write_text(plan.render_report(), encoding="utf-8")
    db.update(conn, "applications", application_id,
              {"readiness_report_path": str(report_path)})

    if plan.screenshot_path:
        db.insert(conn, "screenshots", {
            "entity_type": "application", "entity_id": application_id,
            "path": plan.screenshot_path, "url": url, "phase": "pre_submission",
            "notes": "filled state captured before approval"})

    db.update(conn, "targets", target_id, {"stage": "application_ready"})
    db.audit(conn, action="application_prepared",
             summary=f"application #{application_id} prepared for {plan.company}",
             entity_type="application", entity_id=application_id,
             detail={"url": url, "filled": len(plan.filled),
                     "unfilled": len(plan.unfilled),
                     "human_required": plan.human_required})
    return plan


def submit(*_args: Any, **_kwargs: Any) -> None:
    """Not implemented, and deliberately so."""
    raise PermissionError(
        "This system does not submit applications. Prepare the form, screenshot it, "
        "present the READY FOR APPROVAL report, and hand the keyboard to the human.")


def record_submission(conn: sqlite3.Connection, application_id: int, *,
                      submitted_by: str, confirmation_ref: str | None = None,
                      screenshot_path: str | None = None) -> None:
    """Record a submission a HUMAN performed. Requires a prior approval."""
    row = db.one(conn, "SELECT * FROM applications WHERE id = ?", (application_id,))
    if row is None:
        raise ValueError(f"no application with id {application_id}")
    if row["status"] != "approved" or not row["approved_by"]:
        raise PermissionError(
            f"application #{application_id} has not been approved — refusing to record "
            "a submission")
    db.update(conn, "applications", application_id, {
        "status": "submitted", "submitted_at": db.utcnow(),
        "confirmation_ref": confirmation_ref})
    db.update(conn, "targets", int(row["target_id"]), {"stage": "application_submitted"})
    if screenshot_path:
        db.insert(conn, "screenshots", {
            "entity_type": "application", "entity_id": application_id,
            "path": screenshot_path, "url": row["url"], "phase": "confirmation",
            "notes": f"submitted by {submitted_by}"})
    db.audit(conn, action="application_submitted", external=True, actor="human",
             summary=f"application #{application_id} submitted by {submitted_by}",
             entity_type="application", entity_id=application_id,
             detail={"confirmation_ref": confirmation_ref})
