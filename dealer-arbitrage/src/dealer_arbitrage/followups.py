"""Stage 10 — the follow-up engine.

Schedule: 3, 7, 14, 30 days after the first touch. Each follow-up must change
the ANGLE, not repeat the ask — the engine refuses to schedule an angle already
used on that target, and the day-30 note is explicitly the last one.

Sending still runs through the Stage 7 approval gate unless autonomous
follow-ups have been explicitly enabled for that specific company.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from . import approval, db, outreach, paths
from .profile import BusinessProfile

TEMPLATE_BY_DAY = {3: "day3.md", 7: "day7.md", 14: "day14.md", 30: "day30.md"}
ANGLE_BY_DAY = {3: "specific_inventory_question", 7: "program_mechanics",
                14: "right_person_referral", 30: "close_the_loop"}


@dataclass
class ScheduledFollowup:
    followup_id: int
    target_id: int
    day_offset: int
    due_at: str
    angle: str
    template: str


def schedule(conn: sqlite3.Connection, target_id: int, outreach_id: int, *,
             settings: Mapping[str, Any] | None = None,
             from_date: datetime | None = None) -> list[ScheduledFollowup]:
    """Lay out the follow-up ladder for one sent message."""
    settings = settings or json.loads(paths.SETTINGS.read_text(encoding="utf-8"))
    days = settings.get("followups", {}).get("schedule_days", [3, 7, 14, 30])
    anchor = from_date or datetime.now(timezone.utc)

    used_angles = {row["angle"] for row in
                   db.rows(conn, "SELECT angle FROM followups WHERE target_id = ?",
                           (target_id,))}
    used_angles |= {row["angle"] for row in
                    db.rows(conn, "SELECT angle FROM outreach WHERE target_id = ?"
                                  " AND angle IS NOT NULL", (target_id,))}

    scheduled: list[ScheduledFollowup] = []
    for day in days:
        angle = ANGLE_BY_DAY.get(day, f"day{day}")
        if settings.get("followups", {}).get("require_new_angle", True) \
                and angle in used_angles:
            continue
        due = (anchor + timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S")
        followup_id = db.insert(conn, "followups", {
            "target_id": target_id, "outreach_id": outreach_id, "day_offset": day,
            "due_at": due, "angle": angle,
            "template_key": TEMPLATE_BY_DAY.get(day, ""), "status": "scheduled",
        })
        used_angles.add(angle)
        scheduled.append(ScheduledFollowup(followup_id, target_id, day, due, angle,
                                           TEMPLATE_BY_DAY.get(day, "")))

    db.audit(conn, action="followups_scheduled",
             summary=f"{len(scheduled)} follow-ups scheduled for target #{target_id}",
             entity_type="target", entity_id=target_id,
             detail={"days": [s.day_offset for s in scheduled],
                     "angles": [s.angle for s in scheduled]})
    return scheduled


def due(conn: sqlite3.Connection, as_of: datetime | None = None) -> list[sqlite3.Row]:
    """Follow-ups that are ripe, excluding targets that have gone quiet for good."""
    now = (as_of or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
    return db.rows(conn,
                   "SELECT f.*, c.name AS company, t.stage, t.score"
                   " FROM followups f"
                   " JOIN targets t ON t.id = f.target_id"
                   " JOIN companies c ON c.id = t.company_id"
                   " WHERE f.status = 'scheduled' AND f.due_at <= ?"
                   "   AND t.stage NOT IN ('lost','disqualified','won','on_hold')"
                   "   AND NOT EXISTS (SELECT 1 FROM contacts ct"
                   "                    WHERE ct.company_id = t.company_id"
                   "                      AND ct.opted_out = 1)"
                   " ORDER BY t.score DESC, f.due_at", (now,))


def draft_due(conn: sqlite3.Connection, profile: BusinessProfile, *,
              settings: Mapping[str, Any] | None = None,
              limit: int = 10) -> list[outreach.Draft]:
    """Draft every ripe follow-up. Drafting only — sending needs approval."""
    settings = settings or json.loads(paths.SETTINGS.read_text(encoding="utf-8"))
    builder = outreach.OutreachBuilder(profile, settings)
    drafts: list[outreach.Draft] = []

    for row in due(conn)[:limit]:
        # A response since the original send means the ladder is superseded.
        responded = db.scalar(conn,
                              "SELECT COUNT(*) FROM responses WHERE target_id = ?",
                              (row["target_id"],), 0)
        if responded:
            db.update(conn, "followups", int(row["id"]),
                      {"status": "superseded",
                       "skipped_reason": "target has responded — handle the reply instead"})
            continue

        original = db.one(conn, "SELECT subject, sent_at FROM outreach WHERE id = ?",
                          (row["outreach_id"],)) if row["outreach_id"] else None
        sequence_no = int(db.scalar(conn, "SELECT COUNT(*) FROM outreach"
                                          " WHERE target_id = ?", (row["target_id"],), 0)) + 1

        draft = builder.build(
            conn, int(row["target_id"]), template_name=row["template_key"],
            angle=row["angle"], sequence_no=sequence_no,
            extra_context={
                "original_subject": (original["subject"] if original else "my note"),
                "sent_date": ((original["sent_at"] or "")[:10] if original else "recently"),
            })
        outreach_id = outreach.save_draft(conn, draft, sequence_no=sequence_no)
        db.update(conn, "followups", int(row["id"]),
                  {"status": "drafted", "draft_outreach_id": outreach_id})
        drafts.append(draft)

    return drafts


def stop_ladder(conn: sqlite3.Connection, target_id: int, reason: str) -> int:
    """Cancel remaining follow-ups — on rejection, opt-out, or a win."""
    cursor = conn.execute(
        "UPDATE followups SET status='cancelled', skipped_reason=?"
        " WHERE target_id = ? AND status IN ('scheduled','drafted')", (reason, target_id))
    db.audit(conn, action="followups_stopped",
             summary=f"follow-ups stopped for target #{target_id}: {reason}",
             entity_type="target", entity_id=target_id)
    return cursor.rowcount


def can_send_autonomously(conn: sqlite3.Connection, followup_id: int,
                          settings: Mapping[str, Any]) -> tuple[bool, str]:
    """Autonomous follow-ups need the flag AND the company on the allowlist."""
    if not settings.get("autonomy", {}).get("autonomous_followups_enabled"):
        return False, ("autonomous_followups_enabled is false — this follow-up needs "
                       "approval like everything else")
    row = db.one(conn, "SELECT c.name FROM followups f JOIN targets t ON t.id = f.target_id"
                       " JOIN companies c ON c.id = t.company_id WHERE f.id = ?",
                 (followup_id,))
    if row is None:
        return False, f"no follow-up with id {followup_id}"
    return approval.autonomous_send_allowed(settings, row["name"], "outreach")
