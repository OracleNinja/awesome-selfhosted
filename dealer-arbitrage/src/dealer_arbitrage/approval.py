"""Stage 7 — the outreach approval gate.

Nothing leaves this system without passing through here. The gate prints a
fixed-shape block so a human can read the same fields every time and answer with
one of three words: APPROVE SEND, EDIT, CANCEL.

Enforcement is layered, because a single check is a single point of failure:
  * this module refuses to record a send without an APPROVE SEND decision,
  * the database triggers refuse the same thing independently,
  * autonomous sending requires BOTH a config flag and a per-company allowlist
    entry, and purchases/offer-acceptance cannot be automated at all.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import db, landed_cost, paths

APPROVE = "APPROVE SEND"
EDIT = "EDIT"
CANCEL = "CANCEL"
VALID_DECISIONS = (APPROVE, EDIT, CANCEL)

# Actions that can NEVER be automated, regardless of configuration.
NEVER_AUTONOMOUS = ("purchase", "offer_acceptance", "application_submission",
                    "financing_agreement", "contract_signature")


class ApprovalError(RuntimeError):
    pass


@dataclass
class ApprovalRequest:
    entity_type: str
    entity_id: int
    target: str
    company: str
    contact: str
    channel: str
    message: str
    attachments: list[str]
    request: str
    expected_outcome: str
    risks: list[str]
    cost_commitment_cents: int
    warnings: list[str]
    approval_id: int | None = None

    def render(self) -> str:
        bar = "=" * 78
        lines = [bar, "OUTREACH APPROVAL REQUIRED", bar,
                 f"TARGET:                 #{self.entity_id} ({self.entity_type}) — {self.target}",
                 f"COMPANY:                {self.company}",
                 f"CONTACT:                {self.contact}",
                 f"CHANNEL:                {self.channel}",
                 "MESSAGE:", ""]
        lines += [("    " + line) if line.strip() else ""
                  for line in self.message.rstrip().splitlines()]
        lines += ["",
                  "ATTACHMENTS:            " + (", ".join(self.attachments)
                                                if self.attachments else "none"),
                  f"REQUEST:                {self.request or '(none stated)'}",
                  f"EXPECTED OUTCOME:       {self.expected_outcome or '(not stated)'}",
                  "RISKS:"]
        lines += [f"  - {risk}" for risk in (self.risks or ["(none identified)"])]
        lines.append("TOTAL COST COMMITMENT:  "
                     + landed_cost.fmt(self.cost_commitment_cents))
        if self.cost_commitment_cents:
            lines.append("  !! This message commits money. Read the commitment line again.")
        if self.warnings:
            lines.append("")
            lines.append("WARNINGS:")
            lines += [f"  !! {w}" for w in self.warnings]
        lines += ["", bar,
                  "Reply with:  APPROVE SEND   |   EDIT   |   CANCEL",
                  bar]
        return "\n".join(lines)


def present(conn: sqlite3.Connection, entity_type: str, entity_id: int,
            *, extra_warnings: Sequence[str] = ()) -> ApprovalRequest:
    """Build the approval block and park the entity in 'pending_approval'."""
    if entity_type == "outreach":
        request = _present_outreach(conn, entity_id, extra_warnings)
    elif entity_type == "application":
        request = _present_application(conn, entity_id, extra_warnings)
    else:
        raise ApprovalError(f"unsupported approval entity type: {entity_type}")

    approval_id = db.insert(conn, "approvals", {
        "entity_type": entity_type, "entity_id": entity_id,
        "presented_at": db.utcnow(), "presented_body": request.render(),
        "cost_commitment_cents": request.cost_commitment_cents,
    })
    request.approval_id = approval_id

    if entity_type == "outreach":
        db.update(conn, "outreach", entity_id,
                  {"status": "pending_approval", "approval_id": approval_id})
        target_id = db.scalar(conn, "SELECT target_id FROM outreach WHERE id = ?",
                              (entity_id,), None)
        if target_id:
            db.update(conn, "targets", int(target_id), {"stage": "awaiting_approval"})
    else:
        db.update(conn, "applications", entity_id, {"status": "ready_for_approval"})

    db.add_task(conn, title=f"Approve or reject {entity_type} #{entity_id} — {request.company}",
                detail=f"Awaiting {APPROVE} / {EDIT} / {CANCEL}",
                kind="approval", priority=1,
                entity_type=entity_type, entity_id=entity_id)
    db.audit(conn, action="approval_requested",
             summary=f"{entity_type} #{entity_id} presented for approval "
                     f"({request.company})",
             entity_type=entity_type, entity_id=entity_id, approval_id=approval_id,
             detail={"cost_commitment_cents": request.cost_commitment_cents,
                     "warnings": request.warnings})
    return request


def _present_outreach(conn: sqlite3.Connection, outreach_id: int,
                      extra_warnings: Sequence[str]) -> ApprovalRequest:
    row = db.one(conn,
                 "SELECT o.*, c.name AS company, c.company_type, t.id AS target_id,"
                 "       t.strategy, t.score"
                 " FROM outreach o JOIN targets t ON t.id = o.target_id"
                 " JOIN companies c ON c.id = t.company_id WHERE o.id = ?", (outreach_id,))
    if row is None:
        raise ApprovalError(f"no outreach with id {outreach_id}")
    if row["status"] == "sent":
        raise ApprovalError(f"outreach #{outreach_id} has already been sent")

    contact = None
    if row["contact_id"]:
        contact = db.one(conn, "SELECT * FROM contacts WHERE id = ?", (row["contact_id"],))
    contact_label = "(general inbox / web form)"
    if contact:
        contact_label = (f"{contact['full_name'] or '(unnamed)'}"
                         f"{', ' + contact['role'] if contact['role'] else ''}"
                         f" <{contact['email'] or contact['phone'] or 'no channel'}>")

    warnings = list(extra_warnings)
    if contact and contact["opted_out"]:
        warnings.append("CONTACT HAS OPTED OUT — do not approve this send.")
    if not db.loads(row["personalization"], []):
        warnings.append("No evidenced personalization recorded — this reads as a form letter.")
    claims = db.loads(row["claims_asserted"], []) or []
    for claim in claims:
        supported = db.scalar(conn,
                              "SELECT COUNT(*) FROM sources s JOIN targets t"
                              " ON t.company_id = s.company_id"
                              " WHERE t.id = ? AND s.claim = ?",
                              (row["target_id"], claim), 0)
        if not supported:
            warnings.append(f"Claim '{claim}' has no stored evidence row.")
    if row["sequence_no"] and int(row["sequence_no"]) > 3:
        warnings.append(f"This is touch #{row['sequence_no']} — consider stopping instead.")

    body = row["body"] or ""
    subject = f"Subject: {row['subject']}\n\n" if row["subject"] else ""
    return ApprovalRequest(
        entity_type="outreach", entity_id=outreach_id,
        target=f"target #{row['target_id']} (score {row['score']}, "
               f"strategy {row['strategy'] or 'unassigned'})",
        company=row["company"], contact=contact_label, channel=row["channel"],
        message=subject + body,
        attachments=_attachment_labels(conn, db.loads(row["attachments"], []) or []),
        request=row["ask"] or "", expected_outcome=row["expected_outcome"] or "",
        risks=_risk_list(row["risks"]),
        cost_commitment_cents=int(row["cost_commitment_cents"] or 0),
        warnings=warnings)


def _present_application(conn: sqlite3.Connection, application_id: int,
                         extra_warnings: Sequence[str]) -> ApprovalRequest:
    row = db.one(conn,
                 "SELECT a.*, c.name AS company, t.id AS target_id"
                 " FROM applications a JOIN targets t ON t.id = a.target_id"
                 " JOIN companies c ON c.id = t.company_id WHERE a.id = ?",
                 (application_id,))
    if row is None:
        raise ApprovalError(f"no application with id {application_id}")

    filled = db.loads(row["filled_fields"], {}) or {}
    unfilled = db.loads(row["unfilled_fields"], {}) or {}
    human = db.loads(row["human_required"], []) or []
    message_lines = [f"Application: {row['program_name'] or '(unnamed)'}",
                     f"URL: {row['url']}", "", "FIELDS TO BE SUBMITTED:"]
    message_lines += [f"  {k} = {v}" for k, v in filled.items()]
    if unfilled:
        message_lines += ["", "FIELDS LEFT BLANK (human must supply or confirm):"]
        message_lines += [f"  {k} — {v}" for k, v in unfilled.items()]
    if human:
        message_lines += ["", "REQUIRES A HUMAN AT THE KEYBOARD:"]
        message_lines += [f"  {item}" for item in human]

    warnings = list(extra_warnings)
    if unfilled:
        warnings.append(f"{len(unfilled)} field(s) are blank — submitting now sends an "
                        "incomplete application.")
    if human:
        warnings.append("This form has a login, CAPTCHA, signature or payment step. "
                        "The agent will not attempt it.")

    return ApprovalRequest(
        entity_type="application", entity_id=application_id,
        target=f"target #{row['target_id']}", company=row["company"],
        contact="(application form)", channel="web_form",
        message="\n".join(message_lines),
        attachments=_attachment_labels(conn, db.loads(row["attachments"], []) or []),
        request="Submit dealer/wholesale application",
        expected_outcome="Application on file; dealer packet and pricing returned.",
        risks=["Submitting an application is a formal representation of the business. "
               "Every field must be true.",
               "Some applications include terms acceptance — read before approving."],
        cost_commitment_cents=0, warnings=warnings)


def _risk_list(value: Any) -> list[str]:
    """Risks are stored as a JSON array; tolerate legacy semicolon-joined text."""
    parsed = db.loads(value, [])
    if isinstance(parsed, list):
        return [str(r) for r in parsed]
    return [r.strip() for r in str(value or "").split(";") if r.strip()]


def _attachment_labels(conn: sqlite3.Connection, doc_ids: Sequence[Any]) -> list[str]:
    labels: list[str] = []
    for doc_id in doc_ids:
        row = db.one(conn, "SELECT label, approved_for_sharing FROM documents WHERE id = ?",
                     (doc_id,))
        if row is None:
            labels.append(f"#{doc_id} (MISSING DOCUMENT RECORD)")
        elif not row["approved_for_sharing"]:
            labels.append(f"{row['label']} (NOT APPROVED FOR SHARING — will be withheld)")
        else:
            labels.append(str(row["label"]))
    return labels


def decide(conn: sqlite3.Connection, approval_id: int, decision: str, *,
           decided_by: str, notes: str | None = None) -> dict[str, Any]:
    """Record a human decision. This is the only path out of 'pending_approval'."""
    decision = decision.strip().upper()
    if decision not in VALID_DECISIONS:
        raise ApprovalError(f"decision must be one of {VALID_DECISIONS}, got {decision!r}")
    if not decided_by or not decided_by.strip():
        raise ApprovalError("decided_by is required — approvals are attributable")

    row = db.one(conn, "SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if row is None:
        raise ApprovalError(f"no approval with id {approval_id}")
    if row["decision"]:
        raise ApprovalError(f"approval #{approval_id} already decided: {row['decision']}")

    db.update(conn, "approvals", approval_id, {
        "decision": decision, "decided_at": db.utcnow(),
        "decided_by": decided_by.strip(), "edit_notes": notes,
    })

    entity_type, entity_id = row["entity_type"], int(row["entity_id"])
    if entity_type == "outreach":
        status = {APPROVE: "approved", EDIT: "edited", CANCEL: "cancelled"}[decision]
        db.update(conn, "outreach", entity_id, {"status": status})
    elif entity_type == "application":
        if decision == APPROVE:
            db.update(conn, "applications", entity_id, {
                "status": "approved", "approved_by": decided_by.strip(),
                "approved_at": db.utcnow()})
        else:
            db.update(conn, "applications", entity_id,
                      {"status": "draft" if decision == EDIT else "cancelled"})

    conn.execute("UPDATE tasks SET status='done', completed_at=? WHERE entity_type=?"
                 " AND entity_id=? AND kind='approval' AND status IN ('open','in_progress')",
                 (db.utcnow(), entity_type, entity_id))

    db.audit(conn, action="approval_decision", actor="human",
             summary=f"{decision} on {entity_type} #{entity_id} by {decided_by}",
             entity_type=entity_type, entity_id=entity_id, approval_id=approval_id,
             detail={"notes": notes})
    return {"approval_id": approval_id, "decision": decision,
            "entity_type": entity_type, "entity_id": entity_id}


def record_sent(conn: sqlite3.Connection, outreach_id: int, *,
                sent_by: str, evidence_path: str | None = None) -> None:
    """Mark outreach as actually sent. Requires a recorded APPROVE SEND."""
    row = db.one(conn, "SELECT * FROM outreach WHERE id = ?", (outreach_id,))
    if row is None:
        raise ApprovalError(f"no outreach with id {outreach_id}")
    approval = db.one(conn, "SELECT * FROM approvals WHERE id = ?", (row["approval_id"],)) \
        if row["approval_id"] else None
    if approval is None or approval["decision"] != APPROVE:
        raise ApprovalError(
            f"outreach #{outreach_id} has no {APPROVE} decision — refusing to record a send")

    db.update(conn, "outreach", outreach_id, {"status": "sent", "sent_at": db.utcnow()})
    target_id = int(row["target_id"])
    db.update(conn, "targets", target_id, {"stage": "contacted"})
    if evidence_path:
        db.insert(conn, "screenshots", {
            "entity_type": "outreach", "entity_id": outreach_id,
            "path": evidence_path, "phase": "post_submission",
            "notes": "copy of the sent message"})
    db.audit(conn, action="outreach_sent", external=True, actor="human",
             summary=f"outreach #{outreach_id} sent by {sent_by}",
             entity_type="outreach", entity_id=outreach_id,
             approval_id=int(row["approval_id"]),
             detail={"channel": row["channel"], "evidence_path": evidence_path})


def autonomous_send_allowed(settings: Mapping[str, Any], company_name: str,
                            action: str = "outreach") -> tuple[bool, str]:
    """Autonomy check. Requires an explicit flag AND an explicit allowlist entry."""
    if action in NEVER_AUTONOMOUS:
        return False, (f"'{action}' can never be autonomous — it requires a human "
                       "decision every time.")
    autonomy = settings.get("autonomy", {})
    if not autonomy.get("autonomous_sending_enabled"):
        return False, ("autonomous_sending_enabled is false in config/settings.json — "
                       "every send needs approval")
    allowlist = [a.lower() for a in autonomy.get("autonomous_sending_allowlist", [])]
    if company_name.lower() not in allowlist:
        return False, (f"'{company_name}' is not in autonomous_sending_allowlist — "
                       "autonomy is per-workflow, not global")
    return True, "explicitly configured for autonomous sending"


def purchase_gate(*_args: Any, **_kwargs: Any) -> None:
    """Hard stop. Purchases are a human action, full stop."""
    raise ApprovalError(
        "This system does not purchase, submit payment, sign agreements, or accept "
        "financial terms. Prepare the details and hand them to the human.")


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.rows(conn, "SELECT * FROM approvals WHERE decision IS NULL"
                         " ORDER BY presented_at")
