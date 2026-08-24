"""Stage 1 and the landed-cost model: what the agent knows, and what things cost."""
from __future__ import annotations

import json

import pytest


def test_template_profile_blocks_outreach(project):
    """The shipped template asserts nothing, so it must block every action."""
    from dealer_arbitrage.profile import BusinessProfile
    template = json.loads((project / "profile" / "business.json").read_text())
    for key in ("legal_name", "owner_name", "business_name", "phone", "email"):
        template[key] = None
    (project / "profile" / "business.json").write_text(json.dumps(template))

    report = BusinessProfile.load().validate("identity")
    assert not report.ok
    assert "legal_name" in report.missing_required


def test_filled_profile_passes_outreach_but_not_application(project):
    from dealer_arbitrage.profile import BusinessProfile
    profile = BusinessProfile.load()
    assert profile.validate("outreach").ok
    # EIN and the sales tax permit number are absent, so an application stays blocked.
    application = profile.validate("dealer_application")
    assert not application.ok
    assert "tax_information.ein" in application.missing_required


def test_profile_never_invents_a_missing_field(project):
    from dealer_arbitrage.profile import BusinessProfile
    profile = BusinessProfile.load()
    assert profile.get("years_in_business") is None
    assert profile.get("licenses.state_dealer_license.number") is None


def test_projections_are_labeled_as_plans(project):
    from dealer_arbitrage.profile import BusinessProfile
    facts = BusinessProfile.load().facts_for_outreach()
    assert "planned" in facts["projected_initial_inventory"]
    assert "projected" in facts["projected_annual_volume"]


def test_security_scan_rejects_credentials_in_profile(project):
    from dealer_arbitrage.profile import BusinessProfile
    data = json.loads((project / "profile" / "business.json").read_text())
    data["password"] = "hunter2"
    data["notes_field"] = "4111111111111111"
    (project / "profile" / "business.json").write_text(json.dumps(data))

    violations = BusinessProfile.load().security_scan()
    assert any("password" in v for v in violations)
    assert any("card number" in v for v in violations)
    assert not BusinessProfile.load().validate("identity").ok


def test_sensitive_values_are_not_read_without_the_local_file(project):
    from dealer_arbitrage.profile import BusinessProfile
    profile = BusinessProfile.load(include_sensitive=True)
    assert profile.is_sensitive("tax_information.ein")
    assert profile.get("tax_information.ein") is None

    (project / "profile" / "sensitive.local.json").write_text(json.dumps({"ein": "12-3456789"}))
    assert BusinessProfile.load(include_sensitive=True).get("tax_information.ein") == "12-3456789"
    # Without the flag, the sensitive value stays out of reach.
    assert BusinessProfile.load().get("tax_information.ein") is None


# --------------------------------------------------------------- landed cost
def test_undisclosed_component_is_never_treated_as_zero(project):
    from dealer_arbitrage import landed_cost
    quote = {"vehicle_price_cents": 0, "setup_prep_cents": 0}
    cost = landed_cost.compute(quote)
    assert cost.total_cents is None
    assert not cost.complete
    assert "Freight / shipping" in cost.undisclosed
    assert "not $0" in cost.render()


def test_confirmed_all_zero_quote_is_complete_and_zero(project):
    from dealer_arbitrage import landed_cost
    quote = {column: 0 for column, _ in landed_cost.COMPONENTS}
    cost = landed_cost.compute(quote)
    assert cost.complete and cost.is_zero


def test_incomplete_quote_never_outranks_a_complete_one(project):
    from dealer_arbitrage import landed_cost
    complete = {column: 0 for column, _ in landed_cost.COMPONENTS}
    complete["vehicle_price_cents"] = 500_00
    incomplete = {"vehicle_price_cents": 0}
    assert landed_cost.rank_key(complete) < landed_cost.rank_key(incomplete)


def test_credit_is_reported_as_conditional(project):
    from dealer_arbitrage import landed_cost
    quote = {column: 0 for column, _ in landed_cost.COMPONENTS}
    quote.update({"vehicle_price_cents": 645000, "credit_pct": 100, "min_order_units": 5})
    cost = landed_cost.compute(quote)
    assert cost.net_after_credit_cents == 0
    rendered = cost.render()
    assert "CONDITIONAL" in rendered
    assert "$6,450.00" in rendered      # what is actually owed today


@pytest.mark.parametrize("text,expected", [
    ("$1,250.00", 125000), ("free", 0), ("included", 0), ("waived", 0),
    ("TBD", None), ("unknown", None), ("", None), (None, None), ("950", 95000),
])
def test_money_parsing_distinguishes_zero_from_unknown(project, text, expected):
    from dealer_arbitrage import landed_cost
    assert landed_cost.parse_money(text) == expected
