"""Shared fixtures. Every test runs against a throwaway project root so the
real profile, settings and campaign database are never touched."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A complete, isolated copy of the project tree with a filled sample profile."""
    for name in ("config", "db", "profile", "templates"):
        shutil.copytree(REPO / name, tmp_path / name)
    for stale in (tmp_path / "db").glob("*.db*"):
        stale.unlink()
    for sub in ("screenshots", "exports", "evidence", "drafts"):
        (tmp_path / "var" / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("DEALER_ARBITRAGE_HOME", str(tmp_path))
    for module in [m for m in list(sys.modules) if m.startswith("dealer_arbitrage")]:
        del sys.modules[module]

    profile_path = tmp_path / "profile" / "business.json"
    data = json.loads(profile_path.read_text())
    data.update({
        "legal_name": "Test Carts LLC", "owner_name": "Test Owner",
        "business_name": "Test Carts", "phone": "555-0100",
        "email": "owner@example.invalid", "website": "https://example.invalid",
        "business_address": "1 Test Rd", "city": "Testville", "state": "TX",
        "zip": "75001", "business_description": "a new golf-cart sales and service operation",
        "current_products": ["used parts"], "marketing_channels": ["Facebook Marketplace"],
        "storefront_description": "a 2-bay shop with fenced display",
    })
    data["territory"]["primary_market"] = "the Testville area"
    data["display_area"].update({"type": "fenced lot", "square_feet": 1800})
    data["shipping_capability"]["receiving_method"] = "LTL with liftgate"
    data["projected_initial_inventory"].update({"units": 6, "timeframe": "90 days"})
    data["projected_annual_volume"].update({"units": 40, "basis": "year-one projection"})
    data["service_capabilities"]["in_house_service"] = True
    profile_path.write_text(json.dumps(data, indent=2))

    settings_path = tmp_path / "config" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["campaign"]["home_market"] = {
        "city": "Testville", "state": "TX", "zip": "75001", "metro": "Test Metro",
        "metro_cities": ["Testville", "Neighbor City"]}
    settings_path.write_text(json.dumps(settings, indent=2))
    return tmp_path


@pytest.fixture()
def conn(project):
    from dealer_arbitrage import db
    db.init_db()
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture()
def manufacturer(conn):
    """A researched manufacturer with full evidence — the happy-path target."""
    from dealer_arbitrage import discovery
    result = discovery.intake(conn, discovery.Finding.from_dict({
        "company": "Evidence Motors", "website": "https://mfg.example.invalid",
        "company_type": "manufacturer", "email": "sales@mfg.example.invalid",
        "contact_person": "Dana Rivera", "role": "Dealer Development",
        "has_dealer_program": True, "has_demo_program": True,
        "wholesale_status": "evidence_wholesale",
        "discovery_query": "six passenger lithium golf cart manufacturers",
        "products": [{"brand": "Evidence", "model": "Sixer", "passenger_capacity": 6,
                      "powertrain": "lithium", "condition": "new"}],
        "evidence": [
            {"claim": "dealer_program", "url": "https://mfg.example.invalid/dealers",
             "quoted_text": "Become a dealer: we support new dealers with an application "
                            "and territory assignment."},
            {"claim": "demo_program", "url": "https://mfg.example.invalid/dealers",
             "quoted_text": "Qualified dealers may receive a demo unit for their location."},
            {"claim": "wholesale", "url": "https://mfg.example.invalid/wholesale",
             "quoted_text": "Wholesale and dealer pricing available."},
            {"claim": "company_type", "url": "https://mfg.example.invalid/about",
             "quoted_text": "We manufacture electric low-speed vehicles."}],
    }))
    return result
