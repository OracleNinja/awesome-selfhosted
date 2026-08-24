"""Discovery, scoring, strategy: what gets into the system and how it ranks."""
from __future__ import annotations

import pytest


def test_intake_rejects_unevidenced_status_claims(conn):
    """A claim without a source is downgraded to 'unknown', never stored as fact."""
    from dealer_arbitrage import db, discovery
    result = discovery.intake(conn, discovery.Finding.from_dict({
        "company": "Unsupported Claims Inc", "company_type": "dealer",
        "dealer_status": "evidence_authorized_dealer",
        "wholesale_status": "evidence_wholesale",
        "has_demo_program": True, "has_dealer_program": True,
        "evidence": [],
    }))
    assert len(result.rejected_claims) == 4

    company = db.one(conn, "SELECT * FROM companies WHERE id = ?", (result.company_id,))
    assert company["dealer_status"] == "unknown"
    assert company["wholesale_status"] == "unknown"
    assert company["has_demo_program"] == 0
    assert company["has_dealer_program"] == 0

    # And the gap becomes actionable work rather than silently vanishing.
    tasks = db.rows(conn, "SELECT * FROM tasks WHERE blocking_reason = 'missing_evidence'")
    assert tasks


def test_evidence_requires_url_and_quotation(conn, manufacturer):
    from dealer_arbitrage import evidence
    with pytest.raises(ValueError, match="source URL"):
        evidence.record_source(conn, company_id=manufacturer.company_id, url="",
                               claim="wholesale", quoted_text="they do wholesale")
    with pytest.raises(ValueError, match="exact supporting text"):
        evidence.record_source(conn, company_id=manufacturer.company_id,
                               url="https://x.example.invalid", claim="wholesale",
                               quoted_text="")


def test_unevidenced_signal_scores_zero_and_raises_a_task(conn):
    from dealer_arbitrage import db, discovery, scoring
    unevidenced = discovery.intake(conn, discovery.Finding.from_dict({
        "company": "No Proof Motors", "company_type": "manufacturer",
        "email": "x@example.invalid", "contact_person": "Sam Doe",
        "notes": "site mentions a demo program and wholesale pricing",
    }))
    result = scoring.score_and_store(conn, unevidenced.company_id)
    suppressed = {hit.key for hit in result.suppressed}
    assert "manufacturer_or_distributor" in suppressed
    assert all(hit.key != "manufacturer_or_distributor" for hit in result.positives)
    assert db.rows(conn, "SELECT 1 FROM tasks WHERE title LIKE 'Find evidence:%'")


def test_evidenced_manufacturer_outscores_a_bare_listing(conn, manufacturer):
    from dealer_arbitrage import discovery, scoring
    bare = discovery.intake(conn, discovery.Finding.from_dict({
        "company": "Bare Listing Carts", "company_type": "dealer",
        "city": "Testville", "state": "TX", "wholesale_status": "retail_only",
    }))
    good = scoring.score_and_store(conn, manufacturer.company_id)
    poor = scoring.score_and_store(conn, bare.company_id)
    assert good.score > poor.score
    assert good.p_credited_unit > good.p_free_unit   # credit is the achievable path


def test_scam_markers_drive_the_score_to_the_floor(conn):
    from dealer_arbitrage import discovery, scoring
    sketchy = discovery.intake(conn, discovery.Finding.from_dict({
        "company": "Wire Only Carts", "company_type": "dealer",
        "risk_flags": ["no_address", "new_domain", "wire_only", "price_too_good"],
    }))
    result = scoring.score_and_store(conn, sketchy.company_id)
    assert result.score == 0
    assert any(hit.key == "suspicious_website" for hit in result.penalties)


def test_opt_out_disqualifies_the_target(conn, manufacturer):
    from dealer_arbitrage import db, scoring
    conn.execute("UPDATE contacts SET opted_out = 1 WHERE company_id = ?",
                 (manufacturer.company_id,))
    result = scoring.score_and_store(conn, manufacturer.company_id)
    assert result.disqualify
    target = db.one(conn, "SELECT stage FROM targets WHERE id = ?", (result.target_id,))
    assert target["stage"] == "disqualified"


def test_strategy_never_auto_selects_a_retail_purchase(conn):
    from dealer_arbitrage.strategy import StrategyEngine
    engine = StrategyEngine()
    # A target with no supporting signal at all.
    choice = engine.choose("unknown", [])
    assert choice.key == "retail_purchase"
    assert choice.blocked                      # surfaced, but not actionable by the agent
    assert choice.requires_human_approval


def test_strategy_prefers_the_sponsored_unit_for_an_evidenced_manufacturer(conn, manufacturer):
    from dealer_arbitrage import scoring, strategy
    result = scoring.score_and_store(conn, manufacturer.company_id)
    choice = strategy.assign_strategy(conn, result.target_id, result)
    assert choice.key == "manufacturer_sponsored_evaluation"
    assert choice.rank == 1
    assert choice.target_landed_cost_cents == 0
    assert not choice.requires_human_approval  # asking for a free unit commits nothing


def test_strategies_that_cost_money_demand_approval(conn):
    from dealer_arbitrage.strategy import StrategyEngine
    engine = StrategyEngine()
    choice = engine.choose("wholesaler", ["excess_inventory_signal"])
    assert choice.requires_human_approval
    assert choice.approval_reason


def test_concession_ladder_walks_down_in_order(conn):
    from dealer_arbitrage.strategy import StrategyEngine
    engine = StrategyEngine()
    assert engine.next_concession([])["key"] == "free_vehicle"
    assert engine.next_concession(["free_vehicle"])["key"] == "free_freight"
    assert engine.next_concession(["free_vehicle", "free_freight"])["key"] \
        == "full_first_order_credit"
    everything = [item["key"] for item in engine.ladder()]
    assert engine.next_concession(everything) is None


def test_query_plan_skips_placeholders_instead_of_guessing(project):
    import json
    from dealer_arbitrage import discovery, paths
    settings = json.loads(paths.SETTINGS.read_text())
    settings["campaign"]["home_market"] = {"city": None, "state": None, "metro": None}
    paths.SETTINGS.write_text(json.dumps(settings))

    plan = discovery.query_plan()
    assert plan["skipped"], "queries needing an unset home market must be skipped"
    assert all("{" not in item["query"] for item in plan["runnable"])
