"""Stages 8-10 and 12: reading replies, countering, following up, reporting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def contacted(conn, manufacturer):
    """A target that has been researched, approved, and actually contacted."""
    from dealer_arbitrage import approval, outreach, scoring, strategy
    from dealer_arbitrage.profile import BusinessProfile
    result = scoring.score_and_store(conn, manufacturer.company_id)
    strategy.assign_strategy(conn, result.target_id, result)
    draft = outreach.OutreachBuilder(BusinessProfile.load()).build(
        conn, manufacturer.target_id)
    outreach_id = outreach.save_draft(conn, draft)
    request = approval.present(conn, "outreach", outreach_id)
    approval.decide(conn, request.approval_id, "APPROVE SEND", decided_by="Test Owner")
    approval.record_sent(conn, outreach_id, sent_by="Test Owner")
    return {"target_id": manufacturer.target_id, "outreach_id": outreach_id}


# ------------------------------------------------------------- classification
@pytest.mark.parametrize("text,label", [
    ("We can place a demo unit at your location at no cost.", "offering_free_demo"),
    ("We credit 100% of the demo cost toward your first order of 5 units.",
     "offering_credited_demo"),
    ("Demo unit price is $6,450 plus freight.", "offering_paid_demo"),
    ("Are you an authorized dealer for any brands currently?",
     "asking_for_dealer_credentials"),
    ("Please send your business license and resale certificate.",
     "requesting_business_license"),
    ("Our minimum order is a full container, 20 units.", "requesting_initial_order"),
    ("That territory is covered and we're not adding new dealers.", "rejection"),
    ("Not my area — I'll forward this along to Dana.", "referral"),
    ("Send a $2,000 deposit by wire transfer only, no paperwork needed.", "suspicious"),
    ("Best I can do is 15% off MSRP.", "offering_discount"),
    ("ok", "unclear"),
])
def test_classifier_labels_representative_replies(conn, text, label):
    from dealer_arbitrage.responses import classify
    assert classify(text).label == label


def test_a_price_turns_a_free_sounding_offer_into_a_paid_one(conn):
    from dealer_arbitrage.responses import classify
    result = classify("We can do a free demo — the unit is $6,450 at dealer cost.")
    assert result.label == "offering_paid_demo"
    assert result.requires_human


def test_money_always_routes_to_a_human(conn):
    from dealer_arbitrage.responses import classify
    assert classify("Happy to help — it's $3,000.").requires_human


def test_classification_explains_itself(conn):
    from dealer_arbitrage.responses import classify
    result = classify("We credit 100% of the cost toward your first order.")
    assert result.signals
    assert result.next_step["action"]


def test_rejection_stops_the_follow_up_ladder(conn, contacted):
    from dealer_arbitrage import db, followups, responses
    followups.schedule(conn, contacted["target_id"], contacted["outreach_id"])
    responses.record(conn, contacted["target_id"],
                     "We're not adding new dealers. Please remove us from your list.",
                     outreach_id=contacted["outreach_id"])
    remaining = db.rows(conn, "SELECT * FROM followups WHERE target_id = ? AND status ="
                              " 'scheduled'", (contacted["target_id"],))
    assert remaining == []
    assert db.scalar(conn, "SELECT COUNT(*) FROM contacts WHERE opted_out = 1") >= 1


def test_suspicious_reply_flags_the_company(conn, contacted):
    from dealer_arbitrage import db, responses
    responses.record(conn, contacted["target_id"],
                     "Send a $2,000 deposit by wire transfer only to hold the unit.",
                     outreach_id=contacted["outreach_id"])
    company = db.one(conn, "SELECT risk_flags FROM companies WHERE id ="
                           " (SELECT company_id FROM targets WHERE id = ?)",
                     (contacted["target_id"],))
    assert "suspicious_response" in (db.loads(company["risk_flags"], []) or [])


def test_recorded_offer_never_lands_in_an_accepted_state(conn, contacted):
    from dealer_arbitrage import db, responses
    response_id, _ = responses.record(
        conn, contacted["target_id"],
        "The demo is $6,450 plus $950 freight, credited to your first order of 5.",
        outreach_id=contacted["outreach_id"])
    offer_id = responses.offer_from_response(conn, response_id, {
        "vehicle_price_cents": 645000, "freight_cents": 95000, "credit_pct": 100,
        "min_order_units": 5})
    offer = db.one(conn, "SELECT * FROM offers WHERE id = ?", (offer_id,))
    assert offer["status"] == "open"
    assert offer["landed_cost_cents"] is None       # undisclosed costs remain unknown
    assert db.loads(offer["undisclosed_components"], [])


# ---------------------------------------------------------------- negotiation
def test_first_move_opens_at_the_top_of_the_ladder(conn, contacted):
    from dealer_arbitrage.negotiation import NegotiationEngine
    move = NegotiationEngine().next_move(conn, contacted["target_id"])
    assert "no cost" in move.ask
    assert move.requires_human


def test_incomplete_quote_forces_disclosure_before_anything_else(conn, contacted):
    from dealer_arbitrage import responses
    from dealer_arbitrage.negotiation import NegotiationEngine
    response_id, _ = responses.record(conn, contacted["target_id"],
                                      "Demo is $6,450 plus $950 freight.")
    responses.offer_from_response(conn, response_id, {
        "vehicle_price_cents": 645000, "freight_cents": 95000})
    move = NegotiationEngine().next_move(conn, contacted["target_id"])
    assert "complete landed cost" in move.ask
    assert move.must_capture
    assert any("incomplete" in w or "compare" in w for w in move.warnings)


def test_a_confirmed_zero_offer_still_goes_to_a_human(conn, contacted):
    from dealer_arbitrage import landed_cost, responses
    from dealer_arbitrage.negotiation import NegotiationEngine
    response_id, _ = responses.record(conn, contacted["target_id"],
                                      "We'll place a demo unit at no cost, freight included.")
    responses.offer_from_response(conn, response_id,
                                  {column: 0 for column, _ in landed_cost.COMPONENTS})
    move = NegotiationEngine().next_move(conn, contacted["target_id"])
    assert "hand to the human" in move.ask
    assert any("does not accept offers" in w for w in move.warnings)
    assert "Freight terms and who books the carrier" in move.must_capture


def test_negotiation_cites_no_price_without_a_source(conn, contacted):
    from dealer_arbitrage.negotiation import NegotiationEngine
    move = NegotiationEngine().next_move(conn, contacted["target_id"])
    assert move.citations == []      # no pricing evidence stored, so nothing is cited


def test_we_only_offer_what_the_profile_authorizes(conn, contacted):
    from dealer_arbitrage.negotiation import NegotiationEngine
    move = NegotiationEngine().next_move(conn, contacted["target_id"])
    joined = " ".join(move.concessions_available)
    # willing_to_commit_first_order is null in the test profile, so no commitment
    # may be dangled in front of a counterparty.
    assert "first order commitment" not in joined.lower()


# ----------------------------------------------------------------- follow-ups
def test_followups_use_the_configured_schedule_and_rotate_angles(conn, contacted):
    from dealer_arbitrage import followups
    scheduled = followups.schedule(conn, contacted["target_id"], contacted["outreach_id"])
    assert [item.day_offset for item in scheduled] == [3, 7, 14, 30]
    angles = [item.angle for item in scheduled]
    assert len(set(angles)) == 4, "each follow-up must change the angle"


def test_an_angle_is_never_reused_on_the_same_target(conn, contacted):
    from dealer_arbitrage import followups
    followups.schedule(conn, contacted["target_id"], contacted["outreach_id"])
    again = followups.schedule(conn, contacted["target_id"], contacted["outreach_id"])
    assert again == [], "every angle is spent; nothing new should be scheduled"


def test_a_reply_supersedes_pending_followups(conn, contacted):
    from dealer_arbitrage import db, followups, responses
    from dealer_arbitrage.profile import BusinessProfile
    followups.schedule(conn, contacted["target_id"], contacted["outreach_id"],
                       from_date=datetime.now(timezone.utc) - timedelta(days=40))
    responses.record(conn, contacted["target_id"], "Can you send more information?")
    followups.draft_due(conn, BusinessProfile.load())
    statuses = {row["status"] for row in db.rows(
        conn, "SELECT status FROM followups WHERE target_id = ?", (contacted["target_id"],))}
    assert statuses == {"superseded"}


def test_due_followups_draft_but_never_send(conn, contacted):
    from dealer_arbitrage import db, followups
    from dealer_arbitrage.profile import BusinessProfile
    followups.schedule(conn, contacted["target_id"], contacted["outreach_id"],
                       from_date=datetime.now(timezone.utc) - timedelta(days=40))
    drafts = followups.draft_due(conn, BusinessProfile.load())
    assert drafts
    statuses = {row["status"] for row in db.rows(
        conn, "SELECT status FROM outreach WHERE sequence_no > 1")}
    assert statuses == {"draft"}


def test_autonomous_followups_need_both_switches(conn, contacted):
    import json
    from dealer_arbitrage import followups, paths
    scheduled = followups.schedule(conn, contacted["target_id"], contacted["outreach_id"])
    settings = json.loads(paths.SETTINGS.read_text())
    allowed, why = followups.can_send_autonomously(conn, scheduled[0].followup_id, settings)
    assert not allowed and "autonomous_followups_enabled" in why


# ------------------------------------------------------------------ dashboard
def test_dashboard_reports_every_required_metric(conn, contacted):
    from dealer_arbitrage import dashboard
    rendered = dashboard.render_terminal(dashboard.collect(conn))
    for label in ("TOTAL TARGETS", "RESEARCHED", "CONTACTED", "APPLICATIONS READY",
                  "APPLICATIONS SUBMITTED", "RESPONSES", "NEGOTIATIONS",
                  "FREE-UNIT OFFERS", "CREDITED-UNIT OFFERS", "PAID OFFERS",
                  "REJECTIONS", "ESTIMATED LANDED COST", "BEST CURRENT OFFER",
                  "NEXT ACTION", "ZERO-DOLLAR OPPORTUNITIES"):
        assert label in rendered, f"dashboard is missing {label}"


def test_zero_dollar_view_shows_evidenced_demo_programs(conn, contacted):
    from dealer_arbitrage import dashboard
    metrics = dashboard.collect(conn)
    assert any(row["company"] == "Evidence Motors" for row in metrics.zero_dollar)


def test_dashboard_never_headlines_an_incomplete_quote_as_final(conn, contacted):
    from dealer_arbitrage import dashboard, responses
    response_id, _ = responses.record(conn, contacted["target_id"],
                                      "Unit is $6,450 plus $950 freight.")
    responses.offer_from_response(conn, response_id, {
        "vehicle_price_cents": 645000, "freight_cents": 95000})
    metrics = dashboard.collect(conn)
    assert "INCOMPLETE" in metrics.best_current_offer
    assert "incomplete" in metrics.estimated_landed_cost


def test_html_dashboard_is_self_contained(conn, contacted):
    from dealer_arbitrage import dashboard
    html = dashboard.render_html(dashboard.collect(conn))
    assert "<!doctype html>" in html.lower()
    for external in ("http://", "https://cdn", "<script src", "@import"):
        assert external not in html.lower(), f"dashboard must not load {external}"


def test_profile_gaps_become_the_next_action(conn):
    from dealer_arbitrage import dashboard
    metrics = dashboard.collect(conn, profile_gaps=["legal_name", "phone"])
    assert "business.json" in metrics.next_action
