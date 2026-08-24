"""Filesystem layout resolution.

Everything is anchored to the project root so the CLI works from any cwd.
"""
from __future__ import annotations

import os
from pathlib import Path

# src/dealer_arbitrage/paths.py -> project root is three levels up.
ROOT = Path(__file__).resolve().parents[2]


def _root() -> Path:
    override = os.environ.get("DEALER_ARBITRAGE_HOME")
    return Path(override).expanduser().resolve() if override else ROOT


CONFIG_DIR = _root() / "config"
PROFILE_DIR = _root() / "profile"
DB_DIR = _root() / "db"
TEMPLATE_DIR = _root() / "templates"
VAR_DIR = _root() / "var"

SCHEMA_PATH = DB_DIR / "schema.sql"
DB_PATH = Path(os.environ.get("DEALER_ARBITRAGE_DB", DB_DIR / "arbitrage.db"))

BUSINESS_PROFILE = PROFILE_DIR / "business.json"
SENSITIVE_PROFILE = PROFILE_DIR / "sensitive.local.json"

SETTINGS = CONFIG_DIR / "settings.json"
SCORING = CONFIG_DIR / "scoring.json"
STRATEGIES = CONFIG_DIR / "strategies.json"
SEARCH_QUERIES = CONFIG_DIR / "search_queries.json"
PROFILE_REQUIREMENTS = CONFIG_DIR / "profile_requirements.json"

SCREENSHOT_DIR = VAR_DIR / "screenshots"
EXPORT_DIR = VAR_DIR / "exports"
EVIDENCE_DIR = VAR_DIR / "evidence"
DRAFT_DIR = VAR_DIR / "drafts"


def ensure_dirs() -> None:
    for d in (DB_DIR, VAR_DIR, SCREENSHOT_DIR, EXPORT_DIR, EVIDENCE_DIR,
              DRAFT_DIR, PROFILE_DIR / "documents", PROFILE_DIR / "photos"):
        d.mkdir(parents=True, exist_ok=True)
