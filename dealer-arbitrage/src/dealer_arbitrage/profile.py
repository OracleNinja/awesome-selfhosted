"""Stage 1 — the business profile.

This module is the only place the system is allowed to learn facts about the
user's business. If a field is null here, the agent does not know it, and no
downstream module may substitute a guess, an average, or a plausible-sounding
placeholder. A missing field becomes a blocked action plus a human task.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import paths

# Keys that must never appear in the profile at all (Stage 14).
FORBIDDEN_KEY_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"password", r"passwd", r"secret[_-]?key", r"credit[_-]?card", r"\bcvv\b",
        r"card[_-]?number", r"routing[_-]?number", r"account[_-]?number",
        r"\bssn\b", r"social[_-]?security", r"auth[_-]?cookie", r"session[_-]?token",
        r"api[_-]?key", r"private[_-]?key",
    )
]
# Value shapes that look like secrets even under an innocent key name.
FORBIDDEN_VALUE_PATTERNS = [
    (re.compile(r"^\d{13,19}$"), "looks like a payment card number"),
    (re.compile(r"^\d{3}-\d{2}-\d{4}$"), "looks like an SSN"),
    (re.compile(r"^-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key material"),
]

SENSITIVE_MARKER = "$sensitive"


class ProfileError(RuntimeError):
    pass


@dataclass
class FieldStatus:
    path: str
    present: bool
    value: Any = None
    sensitive: bool = False
    note: str | None = None


@dataclass
class ProfileReport:
    """What the agent knows, what it is missing, and what that blocks."""
    level: str
    ok: bool
    missing_required: list[str] = field(default_factory=list)
    missing_recommended: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    security_violations: list[str] = field(default_factory=list)
    blocked_actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level, "ok": self.ok,
            "missing_required": self.missing_required,
            "missing_recommended": self.missing_recommended,
            "present_count": len(self.present),
            "security_violations": self.security_violations,
            "blocked_actions": self.blocked_actions,
        }


class BusinessProfile:
    def __init__(self, data: dict[str, Any], *, sensitive: dict[str, Any] | None = None,
                 source: Path | None = None):
        self.data = data
        self._sensitive = sensitive or {}
        self.source = source

    # ------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: Path | None = None, *, include_sensitive: bool = False
             ) -> "BusinessProfile":
        path = path or paths.BUSINESS_PROFILE
        if not path.exists():
            raise ProfileError(
                f"business profile not found at {path}. "
                "Copy profile/business.json from the template and fill it in.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProfileError(f"{path} is not valid JSON: {exc}") from exc
        sensitive: dict[str, Any] = {}
        if include_sensitive and paths.SENSITIVE_PROFILE.exists():
            sensitive = json.loads(paths.SENSITIVE_PROFILE.read_text(encoding="utf-8"))
        return cls(data, sensitive=sensitive, source=path)

    # ------------------------------------------------------------- access
    def get(self, dotted: str, default: Any = None) -> Any:
        """Resolve a dotted path. Returns default when absent OR explicitly null.

        A `{"$sensitive": "key"}` node resolves to a redacted marker unless the
        profile was loaded with include_sensitive=True and the local file has it.
        """
        node: Any = self.data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                return default
        return self._resolve(node, default)

    def _resolve(self, node: Any, default: Any = None) -> Any:
        if isinstance(node, dict) and SENSITIVE_MARKER in node:
            key = node[SENSITIVE_MARKER]
            if key in self._sensitive and self._sensitive[key] not in (None, ""):
                return self._sensitive[key]
            return default
        if node in (None, "", [], {}):
            return default
        return node

    def has(self, dotted: str) -> bool:
        return self.get(dotted) is not None

    def is_sensitive(self, dotted: str) -> bool:
        node: Any = self.data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return False
        return isinstance(node, dict) and SENSITIVE_MARKER in node

    def redacted(self, dotted: str) -> str:
        """A form-safe placeholder for a sensitive value the human must supply."""
        return f"[HUMAN TO SUPPLY: {dotted}]"

    # ---------------------------------------------------------- validation
    def validate(self, level: str = "outreach",
                 requirements: dict[str, Any] | None = None) -> ProfileReport:
        reqs = requirements or json.loads(
            paths.PROFILE_REQUIREMENTS.read_text(encoding="utf-8"))
        levels = reqs["levels"]
        if level not in levels:
            raise ProfileError(f"unknown requirement level {level!r}; "
                               f"known: {', '.join(levels)}")

        required = self._expand(level, levels, "required")
        recommended = self._expand(level, levels, "recommended")

        report = ProfileReport(level=level, ok=True)
        for dotted in required:
            (report.present if self._satisfied(dotted) else report.missing_required
             ).append(dotted)
        for dotted in recommended:
            # A field promoted to required at a higher level is not also "recommended".
            if dotted not in required and not self._satisfied(dotted):
                report.missing_recommended.append(dotted)

        report.security_violations = self.security_scan()
        report.ok = not report.missing_required and not report.security_violations
        if report.missing_required:
            report.blocked_actions.append(levels[level]["label"])
        return report

    def _expand(self, level: str, levels: dict[str, Any], key: str) -> list[str]:
        """Walk the extends chain, nearest-first, de-duplicated."""
        seen: list[str] = []
        chain, cursor = [], level
        while cursor:
            chain.append(cursor)
            cursor = levels[cursor].get("extends")
            if cursor in chain:
                raise ProfileError(f"circular extends chain at {cursor!r}")
        for name in reversed(chain):
            for dotted in levels[name].get(key, []):
                if dotted not in seen:
                    seen.append(dotted)
        return seen

    def _satisfied(self, dotted: str) -> bool:
        """A boolean `held: false` counts as answered; a null does not."""
        node: Any = self.data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return False
        if isinstance(node, dict) and SENSITIVE_MARKER in node:
            key = node[SENSITIVE_MARKER]
            return key in self._sensitive and self._sensitive[key] not in (None, "")
        if isinstance(node, bool):
            return True
        return node not in (None, "", [], {})

    def security_scan(self) -> list[str]:
        """Stage 14: refuse to hold credentials or payment data in the profile."""
        violations: list[str] = []

        def walk(node: Any, trail: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    path = f"{trail}.{key}" if trail else key
                    if key == SENSITIVE_MARKER:
                        continue
                    for pattern in FORBIDDEN_KEY_PATTERNS:
                        if pattern.search(key):
                            violations.append(
                                f"{path}: forbidden field — credentials and payment "
                                "data must never be stored in the profile")
                            break
                    walk(value, path)
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    walk(item, f"{trail}[{i}]")
            elif isinstance(node, str):
                for pattern, why in FORBIDDEN_VALUE_PATTERNS:
                    if pattern.match(node.strip()):
                        violations.append(f"{trail}: {why} — remove it")

        walk(self.data, "")
        return violations

    # ------------------------------------------------------- presentation
    def missing_report(self, levels: Iterable[str] = ("identity", "outreach",
                                                      "dealer_application")) -> str:
        reqs = json.loads(paths.PROFILE_REQUIREMENTS.read_text(encoding="utf-8"))
        out: list[str] = []
        for level in levels:
            report = self.validate(level, reqs)
            head = "READY" if report.ok else "BLOCKED"
            out.append(f"\n[{head}] {reqs['levels'][level]['label']}")
            for dotted in report.missing_required:
                out.append(f"    MISSING (required)    {dotted}")
            for dotted in report.missing_recommended:
                out.append(f"    missing (recommended) {dotted}")
            for violation in report.security_violations:
                out.append(f"    !! SECURITY           {violation}")
            if report.ok and not report.missing_recommended:
                out.append("    all fields present")
        return "\n".join(out)

    def facts_for_outreach(self) -> dict[str, Any]:
        """The narrow set of profile facts outreach templates may reference.

        Projections are returned pre-labeled so a template physically cannot
        render them as achieved history.
        """
        init_units = self.get("projected_initial_inventory.units")
        init_time = self.get("projected_initial_inventory.timeframe")
        annual = self.get("projected_annual_volume.units")
        return {
            "business_name": self.get("business_name"),
            "legal_name": self.get("legal_name"),
            "owner_name": self.get("owner_name"),
            "phone": self.get("phone"),
            "email": self.get("email"),
            "website": self.get("website"),
            "city": self.get("city"),
            "state": self.get("state"),
            "market": self.get("territory.primary_market"),
            "radius_miles": self.get("territory.radius_miles"),
            "business_description": self.get("business_description"),
            "years_in_business": self.get("years_in_business"),
            "current_products": self.get("current_products", []),
            "storefront": self.get("storefront_description"),
            "display_area": self.get("display_area.type"),
            "display_sqft": self.get("display_area.square_feet"),
            "service": self.get("service_capabilities.in_house_service"),
            "marketing_channels": self.get("marketing_channels", []),
            "receiving": self.get("shipping_capability.receiving_method"),
            "forklift": self.get("shipping_capability.forklift"),
            "projected_initial_inventory": (
                f"a planned initial order of {init_units} units"
                + (f" within {init_time}" if init_time else "")
                if init_units else None),
            "projected_annual_volume": (
                f"a projected {annual} units per year" if annual else None),
            "is_denago_dealer": bool(self.get("dealer_status.denago_authorized_dealer")),
            "other_brands": self.get("dealer_status.other_authorized_brands", []),
        }


def load(include_sensitive: bool = False) -> BusinessProfile:
    return BusinessProfile.load(include_sensitive=include_sensitive)
