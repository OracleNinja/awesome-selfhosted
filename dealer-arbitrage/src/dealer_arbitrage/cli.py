"""Command-line interface — the machinery behind the /slash skills.

Every command that could touch the outside world stops at a print statement and
a stored approval request. There is no `send`, no `submit`, no `buy`: the verbs
are `prepare`, `present`, and `record`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from . import (applications, approval, dashboard, db, discovery, evidence,
               followups, landed_cost, negotiation, outreach, paths, responses,
               scoring, security, strategy)
from .profile import BusinessProfile, ProfileError


def _conn(args: argparse.Namespace) -> sqlite3.Connection:
    return db.connect(getattr(args, "db", None))


def _profile(include_sensitive: bool = False) -> BusinessProfile:
    return BusinessProfile.load(include_sensitive=include_sensitive)


def _profile_gaps(level: str = "outreach") -> list[str]:
    try:
        return _profile().validate(level).missing_required
    except ProfileError:
        return ["profile/business.json is missing or unreadable"]


# ------------------------------------------------------------------ commands
def cmd_init(args: argparse.Namespace) -> int:
    path = db.init_db(getattr(args, "db", None))
    print(f"Database ready: {path}")
    print(f"Profile template: {paths.BUSINESS_PROFILE}")
    print(f"Settings: {paths.SETTINGS}")
    gaps = _profile_gaps("identity")
    if gaps:
        print(f"\n{len(gaps)} required identity field(s) still empty — "
              "run `da profile check`.")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    try:
        profile = _profile(include_sensitive=args.include_sensitive)
    except ProfileError as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.action == "check":
        print(profile.missing_report())
        violations = profile.security_scan()
        if violations:
            print("\nSECURITY VIOLATIONS — fix before doing anything else:")
            for violation in violations:
                print(f"  !! {violation}")
            return 1
        blocked = profile.validate("outreach")
        print(f"\nOutreach: {'READY' if blocked.ok else 'BLOCKED'}")
        return 0 if blocked.ok else 1

    if args.action == "show":
        facts = profile.facts_for_outreach()
        for key, value in facts.items():
            marker = "  " if value not in (None, [], "") else "??"
            print(f"{marker} {key:<32} {value if value not in (None, []) else '(not set)'}")
        return 0

    if args.action == "requirements":
        requirements = json.loads(paths.PROFILE_REQUIREMENTS.read_text(encoding="utf-8"))
        for name, level in requirements["levels"].items():
            print(f"\n{name}: {level['label']}")
            for dotted in level.get("required", []):
                print(f"    required     {dotted}")
            for dotted in level.get("recommended", []):
                print(f"    recommended  {dotted}")
        return 0
    return 1


def cmd_research(args: argparse.Namespace) -> int:
    if args.action == "plan":
        plan = discovery.query_plan(groups=args.groups or None)
        print(discovery.render_plan(plan, limit=args.limit))
        return 0

    conn = _conn(args)
    try:
        if args.action == "import":
            results = discovery.import_json(conn, args.path)
            created = sum(1 for r in results if r.created)
            print(f"Imported {len(results)} finding(s): {created} new, "
                  f"{len(results) - created} merged.")
            for result in results:
                for claim in result.rejected_claims:
                    print(f"  REJECTED (no evidence): {claim}")
                for warning in result.warnings:
                    print(f"  warning: {warning}")
            return 0

        if args.action == "add":
            finding = discovery.Finding.from_dict(json.loads(args.json))
            result = discovery.intake(conn, finding)
            print(f"{'Created' if result.created else 'Updated'} target "
                  f"#{result.target_id} ({finding.company}); "
                  f"{result.evidence_stored} evidence row(s) stored.")
            for claim in result.rejected_claims:
                print(f"  REJECTED (no evidence): {claim}")
            for warning in result.warnings:
                print(f"  warning: {warning}")
            return 0
    finally:
        conn.close()
    return 1


def cmd_evidence(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        if args.action == "add":
            source_id = evidence.record_source(
                conn, company_id=args.company_id, url=args.url, claim=args.claim,
                quoted_text=args.quote, title=args.title,
                confidence=args.confidence)
            print(f"Evidence #{source_id} recorded for claim '{args.claim}'.")
            return 0
        if args.action == "list":
            rows = db.rows(conn, "SELECT s.*, c.name FROM sources s"
                                 " LEFT JOIN companies c ON c.id = s.company_id"
                                 + (" WHERE s.company_id = ?" if args.company_id else "")
                                 + " ORDER BY s.retrieved_at DESC LIMIT 50",
                           (args.company_id,) if args.company_id else ())
            for row in rows:
                print(f"  #{row['id']:<4} {row['name'] or '(no company)':<28} "
                      f"{row['claim']:<24} {row['url']}")
                print(f"        “{(row['quoted_text'] or '')[:110]}”")
            if not rows:
                print("  (no evidence stored)")
            return 0
    finally:
        conn.close()
    return 1


def cmd_score(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        if args.target_id:
            company_id = db.scalar(conn, "SELECT company_id FROM targets WHERE id = ?",
                                   (args.target_id,), None)
            if not company_id:
                print(f"No target #{args.target_id}")
                return 1
            print(scoring.score_and_store(conn, int(company_id)).render())
            return 0
        results = scoring.score_all(conn)
        if not results:
            print("No companies to score. Run research first.")
            return 0
        for result in results[:args.limit]:
            print(result.render())
            print()
        print(f"Scored {len(results)} target(s).")
        return 0
    finally:
        conn.close()


def cmd_strategy(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        target_ids = ([args.target_id] if args.target_id else
                      [int(r["id"]) for r in db.rows(
                          conn, "SELECT id FROM targets WHERE stage NOT IN"
                                " ('lost','disqualified') ORDER BY score DESC")])
        engine = strategy.StrategyEngine()
        for target_id in target_ids[:args.limit]:
            choice = strategy.assign_strategy(conn, target_id, engine=engine)
            row = db.one(conn, "SELECT c.name FROM targets t JOIN companies c"
                               " ON c.id = t.company_id WHERE t.id = ?", (target_id,))
            print(f"\n#{target_id} {row['name'] if row else ''}")
            print(choice.render())
        return 0
    finally:
        conn.close()


def cmd_outreach(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        profile = _profile()
        builder = outreach.OutreachBuilder(profile)
        draft = builder.build(conn, args.target_id, channel=args.channel,
                              template_name=args.template, contact_id=args.contact_id)
        sequence_no = int(db.scalar(conn, "SELECT COUNT(*) FROM outreach WHERE target_id=?",
                                    (args.target_id,), 0)) + 1
        outreach_id = outreach.save_draft(conn, draft, sequence_no=sequence_no)

        print(draft.render_preview())
        print()
        if draft.guard:
            print(draft.guard.render())
        if draft.missing_fields:
            print("\n  MISSING PROFILE FIELDS: "
                  + ", ".join(sorted(set(draft.missing_fields))))
        if draft.unresolved_placeholders:
            print("\n  UNRESOLVED TEMPLATE PLACEHOLDERS: "
                  + ", ".join(sorted(set(draft.unresolved_placeholders)))
                  + "\n  (each appears in the draft as [MISSING: ...] and must be "
                    "resolved or removed before sending)")
        if draft.blocked:
            print("\n  DRAFT BLOCKED — not presented for approval:")
            for reason in draft.blocked_reasons:
                print(f"    - {reason}")
            print(f"\n  Draft #{outreach_id} saved for editing.")
            return 1

        if args.present:
            request = approval.present(conn, "outreach", outreach_id)
            print("\n" + request.render())
        else:
            print(f"\n  Draft #{outreach_id} saved. Run "
                  f"`da approvals present outreach {outreach_id}` to request approval.")
        return 0
    except ProfileError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        conn.close()


def cmd_approvals(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        if args.action == "list":
            rows = approval.pending(conn)
            if not rows:
                print("Nothing awaiting approval.")
                return 0
            for row in rows:
                print(f"\n--- approval #{row['id']} "
                      f"({row['entity_type']} #{row['entity_id']}) ---")
                print(row["presented_body"])
            return 0

        if args.action == "present":
            request = approval.present(conn, args.entity_type, args.entity_id)
            print(request.render())
            return 0

        if args.action in ("approve", "edit", "cancel"):
            decision = {"approve": approval.APPROVE, "edit": approval.EDIT,
                        "cancel": approval.CANCEL}[args.action]
            result = approval.decide(conn, args.approval_id, decision,
                                     decided_by=args.by, notes=args.notes)
            print(f"Recorded {decision} on {result['entity_type']} "
                  f"#{result['entity_id']} by {args.by}.")
            if decision == approval.APPROVE and result["entity_type"] == "outreach":
                print("Send it, then run `da sent <outreach_id> --by <name>` "
                      "to log the send and start the follow-up ladder.")
            return 0

        if args.action == "sent":
            approval.record_sent(conn, args.outreach_id, sent_by=args.by,
                                 evidence_path=args.evidence)
            row = db.one(conn, "SELECT target_id FROM outreach WHERE id = ?",
                         (args.outreach_id,))
            scheduled = followups.schedule(conn, int(row["target_id"]), args.outreach_id)
            print(f"Outreach #{args.outreach_id} logged as sent.")
            for item in scheduled:
                print(f"  follow-up day {item.day_offset:<3} due {item.due_at[:10]} "
                      f"angle={item.angle}")
            return 0
    except approval.ApprovalError as exc:
        print(f"REFUSED: {exc}")
        return 1
    finally:
        conn.close()
    return 1


def cmd_application(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        if args.action == "prepare":
            profile = _profile(include_sensitive=True)
            settings = json.loads(paths.SETTINGS.read_text(encoding="utf-8"))
            allowed, why = security.domain_allowed(args.url, settings)
            fields = None
            if args.fields:
                fields = [applications.FormField(**f)
                          for f in json.loads(Path(args.fields).read_text(encoding="utf-8"))]
            if not allowed and not fields:
                print(f"Browser automation not permitted here: {why}")
                print("Supply the inspected field list with --fields <file.json>, or add "
                      "the domain to security.browser_automation.allowed_domains.")
                security.pause_for_human(conn, f"browser automation not allowed: {why}",
                                         target_id=args.target_id, url=args.url)
                return 1
            plan = applications.prepare(
                conn, args.target_id, args.url, profile=profile, form_fields=fields,
                program_name=args.program, attachments=args.attach or ())
            print(plan.render_report())
            if plan.application_id and args.present:
                print()
                print(approval.present(conn, "application", plan.application_id).render())
            return 0

        if args.action == "screenshot":
            db.insert(conn, "screenshots", {
                "entity_type": "application", "entity_id": args.application_id,
                "path": args.path, "phase": args.phase})
            db.audit(conn, action="screenshot_recorded",
                     summary=f"screenshot for application #{args.application_id}",
                     entity_type="application", entity_id=args.application_id,
                     detail={"path": args.path, "phase": args.phase})
            print(f"Screenshot recorded for application #{args.application_id}.")
            return 0

        if args.action == "submitted":
            applications.record_submission(
                conn, args.application_id, submitted_by=args.by,
                confirmation_ref=args.ref, screenshot_path=args.screenshot)
            print(f"Application #{args.application_id} recorded as submitted by {args.by}.")
            return 0
    except (PermissionError, ProfileError, ValueError) as exc:
        print(f"REFUSED: {exc}")
        return 1
    finally:
        conn.close()
    return 1


def cmd_responses(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        if args.action == "add":
            body = (Path(args.file).read_text(encoding="utf-8") if args.file
                    else args.text or sys.stdin.read())
            response_id, result = responses.record(
                conn, args.target_id, body, channel=args.channel,
                outreach_id=args.outreach_id)
            print(f"Response #{response_id} recorded.")
            print(result.render())
            return 0

        if args.action == "review":
            rows = db.rows(conn,
                           "SELECT r.*, c.name AS company FROM responses r"
                           " JOIN targets t ON t.id = r.target_id"
                           " JOIN companies c ON c.id = t.company_id"
                           + (" WHERE r.handled = 0" if not args.all else "")
                           + " ORDER BY r.received_at DESC LIMIT 50")
            if not rows:
                print("No responses to review.")
                return 0
            for row in rows:
                step = responses.NEXT_STEPS.get(row["classification"], {})
                print(f"\n--- response #{row['id']} — {row['company']} "
                      f"({row['received_at'][:16]}) ---")
                print(f"  CLASSIFIED: {row['classification']} "
                      f"({(row['classification_confidence'] or 0):.0%})")
                print(f"  NEXT: {step.get('action', row['suggested_next_step'])}")
                if step.get("detail"):
                    print(f"        {step['detail']}")
                body = " ".join((row["raw_body"] or "").split())
                print(f"  BODY: {body[:300]}{'...' if len(body) > 300 else ''}")
            return 0

        if args.action == "handled":
            conn.execute("UPDATE responses SET handled = 1 WHERE id = ?", (args.response_id,))
            db.audit(conn, action="response_handled", actor="human",
                     summary=f"response #{args.response_id} marked handled",
                     entity_type="response", entity_id=args.response_id)
            print(f"Response #{args.response_id} marked handled.")
            return 0
    finally:
        conn.close()
    return 1


def cmd_offer(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        components = json.loads(args.components)
        for key in list(components):
            if key.endswith("_cents") and isinstance(components[key], str):
                components[key] = landed_cost.parse_money(components[key])
        offer_id = responses.offer_from_response(conn, args.response_id, components)
        row = db.one(conn, "SELECT * FROM offers WHERE id = ?", (offer_id,))
        print(f"Offer #{offer_id} recorded.")
        print(landed_cost.compute(row).render())
        print("\n  The agent does not accept offers. Take this to the human.")
        return 0
    finally:
        conn.close()


def cmd_negotiate(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        engine = negotiation.NegotiationEngine()
        move = engine.next_move(conn, args.target_id)
        print(move.render())
        if args.record:
            negotiation_id = negotiation.record_round(
                conn, args.target_id, move, their_position=args.their_position)
            print(f"\n  Recorded as negotiation round #{negotiation_id}.")
        return 0
    finally:
        conn.close()


def cmd_followups(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        if args.action == "list":
            rows = followups.due(conn)
            if not rows:
                print("No follow-ups due.")
                return 0
            for row in rows:
                print(f"  #{row['id']:<4} day {row['day_offset']:<3} "
                      f"{row['company'][:32]:<34} angle={row['angle']:<26} "
                      f"due {row['due_at'][:10]}")
            return 0

        if args.action == "draft":
            drafts = followups.draft_due(conn, _profile(), limit=args.limit)
            if not drafts:
                print("Nothing to draft.")
                return 0
            for draft in drafts:
                print(f"\n--- follow-up draft #{draft.outreach_id} "
                      f"({draft.company}, angle={draft.angle}) ---")
                print(draft.render_preview())
                if draft.blocked:
                    print("  BLOCKED: " + "; ".join(draft.blocked_reasons))
            print(f"\n{len(drafts)} draft(s) saved. Each still needs approval.")
            return 0

        if args.action == "stop":
            n = followups.stop_ladder(conn, args.target_id, args.reason)
            print(f"Cancelled {n} scheduled follow-up(s) for target #{args.target_id}.")
            return 0
    except ProfileError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        conn.close()
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        metrics = dashboard.collect(conn, profile_gaps=_profile_gaps())
        if args.html:
            path = dashboard.write_html(metrics)
            print(f"Dashboard written: {path}")
            if args.serve:
                dashboard.serve(path, port=args.port)
            return 0
        print(dashboard.render_terminal(metrics))
        return 0
    finally:
        conn.close()


def cmd_best_offer(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        best = negotiation.best_offer(conn)
        if not best:
            print("No live offers yet.")
            return 0
        offer, cost = best["offer"], best["cost"]
        print(f"BEST CURRENT OFFER — {best['company']} (offer #{offer['id']})")
        print(f"  type:    {offer['offer_type']}")
        print(f"  product: {offer['product_description'] or '(unspecified)'}")
        print()
        print(cost.render())
        if not cost.complete:
            print("\n  This quote is INCOMPLETE. Get the missing components before "
                  "treating it as the best offer.")
        print("\n  Accepting is a human decision. The agent will not accept it.")
        return 0
    finally:
        conn.close()


def cmd_targets(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        if args.target_id:
            row = db.one(conn, "SELECT t.*, c.* FROM targets t JOIN companies c"
                               " ON c.id = t.company_id WHERE t.id = ?", (args.target_id,))
            if row is None:
                print(f"No target #{args.target_id}")
                return 1
            for key in row.keys():
                value = row[key]
                if value not in (None, ""):
                    print(f"  {key:<24} {value}")
            print("\n  EVIDENCE:")
            for source in db.rows(conn, "SELECT * FROM sources WHERE company_id = ?",
                                  (row["company_id"],)):
                print(f"    [{source['claim']}] {source['url']}")
                print(f"      “{(source['quoted_text'] or '')[:120]}”")
            return 0

        where = " WHERE t.stage = ?" if args.stage else ""
        rows = db.rows(conn, "SELECT t.id, c.name, c.company_type, c.state, t.score,"
                             " t.stage, t.strategy FROM targets t"
                             " JOIN companies c ON c.id = t.company_id" + where +
                             " ORDER BY t.score DESC NULLS LAST LIMIT 100",
                       (args.stage,) if args.stage else ())
        if not rows:
            print("No targets.")
            return 0
        print(f"  {'#':<5}{'COMPANY':<32}{'TYPE':<14}{'ST':<4}{'SCORE':<7}"
              f"{'STAGE':<20}STRATEGY")
        for row in rows:
            print(f"  {row['id']:<5}{str(row['name'])[:31]:<32}"
                  f"{str(row['company_type'])[:13]:<14}{str(row['state'] or '--'):<4}"
                  f"{str(row['score'] if row['score'] is not None else '-'):<7}"
                  f"{str(row['stage'])[:19]:<20}{row['strategy'] or '-'}")
        return 0
    finally:
        conn.close()


def cmd_zero(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        rows = db.rows(conn, "SELECT * FROM zero_dollar_opportunities"
                             " ORDER BY COALESCE(p_free,0) + COALESCE(p_credited,0) DESC,"
                             "          score DESC")
        if not rows:
            print("No zero-dollar opportunities identified yet.\n"
                  "A target qualifies on evidence of a demo or dealer program, a live "
                  "free/credited offer, or a scored likelihood of one.")
            return 0
        print(f"  {'#':<5}{'COMPANY':<32}{'TYPE':<14}{'SCORE':<7}{'FREE%':<7}"
              f"{'CRED%':<7}{'BEST LANDED':<14}STAGE")
        for row in rows:
            landed = (landed_cost.fmt(row["best_landed_cost_cents"])
                      if row["best_landed_cost_cents"] is not None else "-")
            print(f"  {row['target_id']:<5}{str(row['company'])[:31]:<32}"
                  f"{str(row['type'])[:13]:<14}{str(row['score'] or 0):<7}"
                  f"{str(row['p_free'] or 0):<7}{str(row['p_credited'] or 0):<7}"
                  f"{landed:<14}{row['stage']}")
        return 0
    finally:
        conn.close()


def cmd_audit(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        where = " WHERE external = 1" if args.external else ""
        rows = db.rows(conn, f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ?",
                       (args.limit,))
        for row in reversed(rows):
            mark = "EXT" if row["external"] else "   "
            print(f"  {row['ts']}  {mark}  {row['actor']:<7}{row['action']:<26}"
                  f"{row['summary']}")
        return 0
    finally:
        conn.close()


def cmd_tasks(args: argparse.Namespace) -> int:
    conn = _conn(args)
    try:
        rows = db.rows(conn, "SELECT * FROM tasks WHERE status IN ('open','in_progress')"
                             " ORDER BY priority, id")
        if not rows:
            print("No open tasks.")
            return 0
        for row in rows:
            print(f"  #{row['id']:<4} p{row['priority']} [{row['kind']:<8}] {row['title']}")
            if row["blocking_reason"]:
                print(f"        blocked by: {row['blocking_reason']}")
        return 0
    finally:
        conn.close()


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="da", description="Dealer Arbitrage Agent — evaluation-unit acquisition")
    parser.add_argument("--db", help="path to the SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database").set_defaults(func=cmd_init)

    p = sub.add_parser("profile", help="inspect the business profile")
    p.add_argument("action", choices=["check", "show", "requirements"], nargs="?",
                   default="check")
    p.add_argument("--include-sensitive", action="store_true")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("research", help="discovery plan and finding intake")
    p.add_argument("action", choices=["plan", "import", "add"])
    p.add_argument("path", nargs="?", help="JSON file of findings (import)")
    p.add_argument("--json", help="a single finding as JSON (add)")
    p.add_argument("--groups", nargs="*", help="restrict the plan to these query groups")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("evidence", help="store and review evidence")
    p.add_argument("action", choices=["add", "list"])
    p.add_argument("--company-id", type=int)
    p.add_argument("--url")
    p.add_argument("--claim")
    p.add_argument("--quote")
    p.add_argument("--title")
    p.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("score", help="score targets 0-100")
    p.add_argument("target_id", type=int, nargs="?")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("strategy", help="assign acquisition strategies")
    p.add_argument("target_id", type=int, nargs="?")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_strategy)

    p = sub.add_parser("outreach", help="draft personalized outreach")
    p.add_argument("target_id", type=int)
    p.add_argument("--channel", default="email",
                   choices=["email", "phone", "web_form", "linkedin", "sms", "in_person"])
    p.add_argument("--template")
    p.add_argument("--contact-id", type=int)
    p.add_argument("--present", action="store_true",
                   help="immediately present the approval block")
    p.set_defaults(func=cmd_outreach)

    p = sub.add_parser("approvals", help="the Stage 7 approval gate")
    p.add_argument("action", choices=["list", "present", "approve", "edit", "cancel", "sent"])
    p.add_argument("entity_type", nargs="?", choices=["outreach", "application"])
    p.add_argument("entity_id", type=int, nargs="?")
    p.add_argument("--approval-id", type=int, dest="approval_id")
    p.add_argument("--outreach-id", type=int, dest="outreach_id")
    p.add_argument("--by", default="", help="who is approving (required for decisions)")
    p.add_argument("--notes")
    p.add_argument("--evidence", help="path to a saved copy of the sent message")
    p.set_defaults(func=cmd_approvals)

    p = sub.add_parser("application", help="prepare a dealer application")
    p.add_argument("action", choices=["prepare", "screenshot", "submitted"])
    p.add_argument("--target-id", type=int)
    p.add_argument("--url")
    p.add_argument("--program", default="Dealer application")
    p.add_argument("--fields", help="JSON file describing the form fields")
    p.add_argument("--attach", type=int, nargs="*", help="document ids to attach")
    p.add_argument("--application-id", type=int)
    p.add_argument("--path", help="screenshot path")
    p.add_argument("--phase", default="pre_submission",
                   choices=["pre_submission", "post_submission", "confirmation",
                            "evidence", "error"])
    p.add_argument("--by", default="")
    p.add_argument("--ref", help="confirmation reference")
    p.add_argument("--screenshot")
    p.add_argument("--present", action="store_true")
    p.set_defaults(func=cmd_application)

    p = sub.add_parser("responses", help="record and review inbound responses")
    p.add_argument("action", choices=["add", "review", "handled"])
    p.add_argument("--target-id", type=int)
    p.add_argument("--outreach-id", type=int)
    p.add_argument("--response-id", type=int)
    p.add_argument("--channel", default="email",
                   choices=["email", "phone", "web_form", "linkedin", "sms", "in_person"])
    p.add_argument("--text")
    p.add_argument("--file")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_responses)

    p = sub.add_parser("offer", help="record an offer from a response")
    p.add_argument("response_id", type=int)
    p.add_argument("components", help='JSON, e.g. {"vehicle_price_cents":0,'
                                      '"freight_cents":"$950","credit_pct":100}')
    p.set_defaults(func=cmd_offer)

    p = sub.add_parser("negotiate", help="compute the next negotiation move")
    p.add_argument("target_id", type=int)
    p.add_argument("--record", action="store_true")
    p.add_argument("--their-position")
    p.set_defaults(func=cmd_negotiate)

    p = sub.add_parser("followups", help="the 3/7/14/30-day ladder")
    p.add_argument("action", choices=["list", "draft", "stop"])
    p.add_argument("--target-id", type=int)
    p.add_argument("--reason", default="stopped by operator")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_followups)

    p = sub.add_parser("status", help="campaign dashboard")
    p.add_argument("--html", action="store_true")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_status)

    sub.add_parser("best-offer", help="the best live offer by landed cost"
                   ).set_defaults(func=cmd_best_offer)
    sub.add_parser("zero", help="zero-dollar opportunities view"
                   ).set_defaults(func=cmd_zero)
    sub.add_parser("tasks", help="open tasks").set_defaults(func=cmd_tasks)

    p = sub.add_parser("targets", help="list or show targets")
    p.add_argument("target_id", type=int, nargs="?")
    p.add_argument("--stage")
    p.set_defaults(func=cmd_targets)

    p = sub.add_parser("audit", help="the audit trail")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--external", action="store_true", help="external actions only")
    p.set_defaults(func=cmd_audit)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Decision commands need an attributable approver.
    if getattr(args, "command", "") == "approvals" and \
            args.action in ("approve", "edit", "cancel", "sent") and not args.by:
        parser.error(f"`approvals {args.action}` requires --by <your name>")
    if getattr(args, "command", "") == "approvals" and \
            args.action in ("approve", "edit", "cancel") and not args.approval_id:
        parser.error(f"`approvals {args.action}` requires --approval-id")
    if getattr(args, "command", "") == "approvals" and args.action == "sent" \
            and not args.outreach_id:
        parser.error("`approvals sent` requires --outreach-id")
    if getattr(args, "command", "") == "application" and args.action == "prepare" \
            and not (args.target_id and args.url):
        parser.error("`application prepare` requires --target-id and --url")

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except security.HumanActionRequired as exc:
        print(f"\nPAUSED — human action required: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
