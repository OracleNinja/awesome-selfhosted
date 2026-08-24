"""The guarantees that matter: no false claims, no unapproved sends, no purchases.

If any test in this file fails, the system is not safe to run.
"""
from __future__ import annotations

import sqlite3

import pytest


# ------------------------------------------------------------- claim guard
def test_claim_guard_blocks_an_unearned_dealer_claim(conn, manufacturer):
    from dealer_arbitrage import evidence
    from dealer_arbitrage.profile import BusinessProfile
    guard = evidence.ClaimGuard(BusinessProfile.load(),
                                evidence.evidence_index(conn, manufacturer.company_id))
    result = guard.scan("I am an authorized Denago dealer and would like pricing.")
    assert result.blocked
    assert any(v.kind == "self_claim" for v in result.violations)


def test_claim_guard_blocks_invented_history_and_volume(conn, manufacturer):
    from dealer_arbitrage import evidence
    from dealer_arbitrage.profile import BusinessProfile
    guard = evidence.ClaimGuard(BusinessProfile.load(),
                                evidence.evidence_index(conn, manufacturer.company_id))
    for text in ("We sell 200 units a year out of our shop.",
                 "We have 12 years in business in this market.",
                 "We are licensed and bonded."):
        assert guard.scan(text).blocked, f"should have blocked: {text}"


def test_claim_guard_blocks_a_fabricated_competing_offer(conn, manufacturer):
    from dealer_arbitrage import evidence
    from dealer_arbitrage.profile import BusinessProfile
    guard = evidence.ClaimGuard(BusinessProfile.load(),
                                evidence.evidence_index(conn, manufacturer.company_id))
    result = guard.scan("Another distributor has offered me the same unit for less.")
    assert result.blocked
    assert any(v.kind == "fabricated_leverage" for v in result.violations)


def test_claim_guard_blocks_asserting_a_demo_program_without_evidence(conn):
    from dealer_arbitrage import evidence
    from dealer_arbitrage.profile import BusinessProfile
    guard = evidence.ClaimGuard(BusinessProfile.load(), {})
    result = guard.scan("I saw you run a demo program.",
                        asserted_claims=["demo_program"])
    assert result.blocked


def test_claim_guard_passes_a_truthful_draft(conn, manufacturer):
    from dealer_arbitrage import evidence
    from dealer_arbitrage.profile import BusinessProfile
    guard = evidence.ClaimGuard(BusinessProfile.load(),
                                evidence.evidence_index(conn, manufacturer.company_id))
    result = guard.scan(
        "I'm opening a golf-cart sales and service operation and I'm looking for one "
        "evaluation unit before placing an initial inventory order.",
        asserted_claims=["demo_program"])
    assert result.clean


# ----------------------------------------------------------------- outreach
def test_outreach_refuses_to_spam_an_unresearched_target(conn):
    from dealer_arbitrage import discovery, outreach
    from dealer_arbitrage.profile import BusinessProfile
    bare = discovery.intake(conn, discovery.Finding.from_dict({
        "company": "Nothing Known Carts", "company_type": "dealer",
        "email": "info@example.invalid"}))
    draft = outreach.OutreachBuilder(BusinessProfile.load()).build(conn, bare.target_id)
    assert draft.blocked
    assert any("evidenced fact" in reason for reason in draft.blocked_reasons)


def test_outreach_blocks_when_the_profile_is_incomplete(conn, manufacturer):
    import json
    from dealer_arbitrage import outreach, paths
    from dealer_arbitrage.profile import BusinessProfile
    data = json.loads(paths.BUSINESS_PROFILE.read_text())
    data["storefront_description"] = None
    paths.BUSINESS_PROFILE.write_text(json.dumps(data))

    draft = outreach.OutreachBuilder(BusinessProfile.load()).build(
        conn, manufacturer.target_id)
    assert draft.blocked
    assert any("business.json" in reason for reason in draft.blocked_reasons)


def test_outreach_cites_the_evidence_it_researched(conn, manufacturer):
    from dealer_arbitrage import outreach
    from dealer_arbitrage.profile import BusinessProfile
    draft = outreach.OutreachBuilder(BusinessProfile.load()).build(
        conn, manufacturer.target_id)
    assert not draft.blocked
    assert "https://mfg.example.invalid" in draft.body    # the source is quoted inline
    assert draft.personalization
    assert draft.cost_commitment_cents == 0


def test_outreach_stops_at_an_opted_out_contact(conn, manufacturer):
    from dealer_arbitrage import outreach
    from dealer_arbitrage.profile import BusinessProfile
    conn.execute("UPDATE contacts SET opted_out = 1 WHERE company_id = ?",
                 (manufacturer.company_id,))
    draft = outreach.OutreachBuilder(BusinessProfile.load()).build(
        conn, manufacturer.target_id)
    assert draft.blocked
    assert any("opted out" in reason for reason in draft.blocked_reasons)


# ---------------------------------------------------------- approval gate
@pytest.fixture()
def draft_id(conn, manufacturer):
    from dealer_arbitrage import outreach
    from dealer_arbitrage.profile import BusinessProfile
    draft = outreach.OutreachBuilder(BusinessProfile.load()).build(
        conn, manufacturer.target_id)
    return outreach.save_draft(conn, draft)


def test_approval_block_contains_every_required_field(conn, draft_id):
    from dealer_arbitrage import approval
    body = approval.present(conn, "outreach", draft_id).render()
    for header in ("TARGET:", "COMPANY:", "CONTACT:", "CHANNEL:", "MESSAGE:",
                   "ATTACHMENTS:", "REQUEST:", "EXPECTED OUTCOME:", "RISKS:",
                   "TOTAL COST COMMITMENT:"):
        assert header in body, f"approval block is missing {header}"
    assert "APPROVE SEND" in body and "EDIT" in body and "CANCEL" in body


def test_send_is_refused_without_an_approval(conn, draft_id):
    from dealer_arbitrage import approval
    approval.present(conn, "outreach", draft_id)
    with pytest.raises(approval.ApprovalError, match="APPROVE SEND"):
        approval.record_sent(conn, draft_id, sent_by="Test Owner")


def test_send_is_refused_after_a_cancel(conn, draft_id):
    from dealer_arbitrage import approval
    request = approval.present(conn, "outreach", draft_id)
    approval.decide(conn, request.approval_id, "CANCEL", decided_by="Test Owner")
    with pytest.raises(approval.ApprovalError):
        approval.record_sent(conn, draft_id, sent_by="Test Owner")


def test_approval_requires_an_attributable_approver(conn, draft_id):
    from dealer_arbitrage import approval
    request = approval.present(conn, "outreach", draft_id)
    with pytest.raises(approval.ApprovalError, match="decided_by"):
        approval.decide(conn, request.approval_id, "APPROVE SEND", decided_by="")


def test_an_approval_cannot_be_decided_twice(conn, draft_id):
    from dealer_arbitrage import approval
    request = approval.present(conn, "outreach", draft_id)
    approval.decide(conn, request.approval_id, "APPROVE SEND", decided_by="Test Owner")
    with pytest.raises(approval.ApprovalError, match="already decided"):
        approval.decide(conn, request.approval_id, "CANCEL", decided_by="Test Owner")


def test_approved_send_is_recorded_as_an_external_action(conn, draft_id):
    from dealer_arbitrage import approval, db
    request = approval.present(conn, "outreach", draft_id)
    approval.decide(conn, request.approval_id, "APPROVE SEND", decided_by="Test Owner")
    approval.record_sent(conn, draft_id, sent_by="Test Owner")

    row = db.one(conn, "SELECT status, sent_at FROM outreach WHERE id = ?", (draft_id,))
    assert row["status"] == "sent" and row["sent_at"]
    assert db.one(conn, "SELECT 1 FROM audit_log WHERE action='outreach_sent'"
                        " AND external=1")


def test_database_refuses_an_unapproved_send_even_bypassing_python(conn, draft_id):
    """Defence in depth: the schema enforces this independently of the code above."""
    with pytest.raises(sqlite3.IntegrityError, match="APPROVE SEND"):
        conn.execute("UPDATE outreach SET status='sent' WHERE id = ?", (draft_id,))


def test_audit_log_is_append_only(conn, manufacturer):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_log SET summary='rewritten' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE id = 1")


def test_autonomous_sending_is_off_and_needs_two_switches(conn):
    import json
    from dealer_arbitrage import approval, paths
    settings = json.loads(paths.SETTINGS.read_text())
    allowed, why = approval.autonomous_send_allowed(settings, "Evidence Motors")
    assert not allowed and "autonomous_sending_enabled" in why

    settings["autonomy"]["autonomous_sending_enabled"] = True
    allowed, why = approval.autonomous_send_allowed(settings, "Evidence Motors")
    assert not allowed and "allowlist" in why       # flag alone is not enough

    settings["autonomy"]["autonomous_sending_allowlist"] = ["Evidence Motors"]
    allowed, _ = approval.autonomous_send_allowed(settings, "Evidence Motors")
    assert allowed


@pytest.mark.parametrize("action", ["purchase", "offer_acceptance",
                                    "application_submission", "financing_agreement",
                                    "contract_signature"])
def test_some_actions_can_never_be_autonomous(conn, action):
    import json
    from dealer_arbitrage import approval, paths
    settings = json.loads(paths.SETTINGS.read_text())
    settings["autonomy"] = {"autonomous_sending_enabled": True,
                            "autonomous_sending_allowlist": ["Anyone"]}
    allowed, why = approval.autonomous_send_allowed(settings, "Anyone", action)
    assert not allowed and "never be autonomous" in why


def test_purchase_and_acceptance_gates_always_raise(conn):
    from dealer_arbitrage import approval, negotiation
    with pytest.raises(approval.ApprovalError):
        approval.purchase_gate()
    with pytest.raises(PermissionError):
        negotiation.accept_offer(offer_id=1)


# ------------------------------------------------------------- applications
def test_application_never_fills_credentials_signatures_or_payment(conn, manufacturer):
    from dealer_arbitrage import applications
    from dealer_arbitrage.profile import BusinessProfile
    fields = [applications.FormField(name=n, label=l) for n, l in [
        ("password", "Password"), ("esign", "Type your initials to sign"),
        ("agree", "I agree to the dealer terms"),
        ("bank_account", "Bank Account Number"), ("ssn", "Social Security Number"),
        ("dob", "Date of Birth"), ("captcha", "Enter the CAPTCHA")]]
    plan = applications.prepare(conn, manufacturer.target_id,
                                "https://mfg.example.invalid/apply",
                                profile=BusinessProfile.load(), form_fields=fields)
    assert plan.filled == {}
    assert all(m.action == "blocked" for m in plan.fields)
    assert len(plan.human_required) >= len(fields)


def test_application_leaves_unknown_fields_blank(conn, manufacturer):
    from dealer_arbitrage import applications
    from dealer_arbitrage.profile import BusinessProfile
    fields = [applications.FormField(name="years", label="Years in Business"),
              applications.FormField(name="brands", label="Brands Carried Today")]
    plan = applications.prepare(conn, manufacturer.target_id,
                                "https://mfg.example.invalid/apply",
                                profile=BusinessProfile.load(), form_fields=fields)
    assert plan.filled == {}
    assert all(m.action == "human_supply" for m in plan.fields)
    assert "never guessed" in plan.render_report()


def test_application_maps_email_not_address(conn, manufacturer):
    """'Email Address' contains 'address'; it must still map to the email."""
    from dealer_arbitrage import applications
    from dealer_arbitrage.profile import BusinessProfile
    fields = [applications.FormField(name="email", label="Email Address"),
              applications.FormField(name="addr1", label="Street Address")]
    plan = applications.prepare(conn, manufacturer.target_id,
                                "https://mfg.example.invalid/apply",
                                profile=BusinessProfile.load(), form_fields=fields)
    assert plan.filled["email"] == "owner@example.invalid"
    assert plan.filled["addr1"] == "1 Test Rd"


def test_application_flags_misrepresentation_risks_for_confirmation(conn, manufacturer):
    from dealer_arbitrage import applications
    from dealer_arbitrage.profile import BusinessProfile
    fields = [applications.FormField(name="vol", label="Annual Sales Volume (units)")]
    plan = applications.prepare(conn, manufacturer.target_id,
                                "https://mfg.example.invalid/apply",
                                profile=BusinessProfile.load(), form_fields=fields)
    assert plan.fields[0].action == "human_confirm"
    assert "misrepresentation" in plan.fields[0].reason


def test_application_withholds_documents_not_approved_for_sharing(conn, manufacturer):
    from dealer_arbitrage import applications, db
    from dealer_arbitrage.profile import BusinessProfile
    doc_id = db.insert(conn, "documents", {
        "kind": "business_license", "label": "Business license", "path": "/tmp/x.pdf",
        "approved_for_sharing": 0})
    plan = applications.prepare(conn, manufacturer.target_id,
                                "https://mfg.example.invalid/apply",
                                profile=BusinessProfile.load(),
                                form_fields=[applications.FormField(name="city",
                                                                    label="City")],
                                attachments=[doc_id])
    assert plan.attachments == []
    assert plan.withheld_attachments


def test_application_submit_is_not_implemented(conn):
    from dealer_arbitrage import applications
    with pytest.raises(PermissionError, match="does not submit"):
        applications.submit(application_id=1)


def test_submission_cannot_be_recorded_without_approval(conn, manufacturer):
    from dealer_arbitrage import applications, db
    application_id = db.insert(conn, "applications", {
        "target_id": manufacturer.target_id, "url": "https://x.example.invalid",
        "status": "ready_for_approval"})
    with pytest.raises(PermissionError, match="has not been approved"):
        applications.record_submission(conn, application_id, submitted_by="Test Owner")


def test_playwright_adapter_exposes_no_submit_method(conn):
    from dealer_arbitrage import applications
    assert not hasattr(applications.PlaywrightAdapter, "submit")
    assert not hasattr(applications.ManualAdapter, "submit")


# ---------------------------------------------------------------- security
@pytest.mark.parametrize("page,expected", [
    ("Please solve the reCAPTCHA to continue", "CAPTCHA"),
    ("Sign in with your password", "Login required"),
    ("Verify your identity to proceed", "Identity verification"),
    ("Enter your card number to complete purchase", "Payment step"),
    ("Please electronically sign the dealer agreement", "E-signature"),
])
def test_walls_stop_the_agent(conn, page, expected):
    from dealer_arbitrage import security
    wall = security.detect_wall(page)
    assert wall and expected in wall
    with pytest.raises(security.HumanActionRequired):
        security.assert_no_auth_wall(page)


def test_secrets_are_never_stored(conn):
    from dealer_arbitrage import security
    with pytest.raises(security.HumanActionRequired, match="payment card"):
        security.assert_storable("Card 4111 1111 1111 1111 expires soon")
    with pytest.raises(security.HumanActionRequired, match="SSN"):
        security.assert_storable("SSN 123-45-6789")
    security.assert_storable("Perfectly ordinary business reply.")


def test_browser_automation_is_domain_allowlisted(conn):
    import json
    from dealer_arbitrage import paths, security
    settings = json.loads(paths.SETTINGS.read_text())
    allowed, why = security.domain_allowed("https://mfg.example.invalid/apply", settings)
    assert not allowed and "allowed_domains" in why

    settings["security"]["browser_automation"]["allowed_domains"] = ["mfg.example.invalid"]
    allowed, _ = security.domain_allowed("https://mfg.example.invalid/apply", settings)
    assert allowed
    denied, _ = security.domain_allowed("https://other.example.invalid/apply", settings)
    assert not denied


def test_counterparty_fraud_markers_are_detected(conn):
    from dealer_arbitrage import security
    flags = security.scan_counterparty_text(
        "Send a deposit before you view it, wire transfer only, no paperwork needed.")
    assert len(flags) >= 2
