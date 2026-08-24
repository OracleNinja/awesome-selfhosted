"""Stage 12 — campaign dashboard.

Two renderers over one set of metrics: a terminal view for working sessions and
a self-contained HTML file for reading. Both include the ZERO-DOLLAR
OPPORTUNITIES view — the prospects where a free, sponsored or fully-credited
unit still looks reachable.

ESTIMATED LANDED COST reports the best *complete* quote. An offer missing its
freight number is shown as incomplete rather than folded into a headline number
that would read as cheaper than it is.
"""
from __future__ import annotations

import html
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import db, landed_cost, paths


@dataclass
class Metrics:
    total_targets: int = 0
    researched: int = 0
    contacted: int = 0
    applications_ready: int = 0
    applications_submitted: int = 0
    responses: int = 0
    negotiations: int = 0
    free_unit_offers: int = 0
    credited_unit_offers: int = 0
    paid_offers: int = 0
    rejections: int = 0
    estimated_landed_cost: str = "no complete quote yet"
    best_current_offer: str = "none"
    best_offer_detail: dict[str, Any] = field(default_factory=dict)
    next_action: str = "Populate profile/business.json, then run /dealer-research."
    zero_dollar: list[dict[str, Any]] = field(default_factory=list)
    human_queue: list[dict[str, Any]] = field(default_factory=list)
    pipeline: dict[str, int] = field(default_factory=dict)
    top_targets: list[dict[str, Any]] = field(default_factory=list)
    profile_gaps: list[str] = field(default_factory=list)


def collect(conn: sqlite3.Connection,
            profile_gaps: list[str] | None = None) -> Metrics:
    m = Metrics(profile_gaps=profile_gaps or [])

    m.total_targets = int(db.scalar(conn, "SELECT COUNT(*) FROM targets"))
    m.researched = int(db.scalar(
        conn, "SELECT COUNT(*) FROM targets WHERE scored_at IS NOT NULL"))
    m.contacted = int(db.scalar(
        conn, "SELECT COUNT(DISTINCT target_id) FROM outreach WHERE status = 'sent'"))
    m.applications_ready = int(db.scalar(
        conn, "SELECT COUNT(*) FROM applications WHERE status IN"
              " ('ready_for_approval','approved','needs_human')"))
    m.applications_submitted = int(db.scalar(
        conn, "SELECT COUNT(*) FROM applications WHERE status = 'submitted'"))
    m.responses = int(db.scalar(conn, "SELECT COUNT(*) FROM responses"))
    m.negotiations = int(db.scalar(
        conn, "SELECT COUNT(DISTINCT target_id) FROM negotiations"))
    m.free_unit_offers = int(db.scalar(
        conn, "SELECT COUNT(*) FROM offers WHERE offer_type IN"
              " ('free_unit','free_unit_plus_freight')"))
    # Credited counts by the CREDIT, not the label: a "paid demo" carrying a 100%
    # first-order credit is the net-zero structure we are actually chasing, and it
    # would otherwise hide in the paid bucket. The three buckets stay disjoint so
    # the tiles add up.
    m.credited_unit_offers = int(db.scalar(
        conn, "SELECT COUNT(*) FROM offers WHERE offer_type = 'credited_demo'"
              "    OR COALESCE(credit_pct, 0) >= 100"))
    m.paid_offers = int(db.scalar(
        conn, "SELECT COUNT(*) FROM offers WHERE COALESCE(credit_pct, 0) < 100"
              "   AND offer_type IN"
              " ('paid_demo','discounted_purchase','retail','previous_model_year',"
              "  'excess_inventory','blemished','cancelled_order')"))
    m.rejections = int(db.scalar(
        conn, "SELECT COUNT(*) FROM responses WHERE classification = 'rejection'"))

    m.pipeline = {row["stage"]: int(row["n"]) for row in db.rows(
        conn, "SELECT stage, COUNT(*) AS n FROM targets GROUP BY stage ORDER BY n DESC")}

    offers = db.rows(conn, "SELECT o.*, c.name AS company FROM offers o"
                           " JOIN targets t ON t.id = o.target_id"
                           " JOIN companies c ON c.id = t.company_id"
                           " WHERE o.status IN ('open','countered',"
                           "                    'accepted_pending_approval','accepted')")
    if offers:
        best = min(offers, key=landed_cost.rank_key)
        cost = landed_cost.compute(best)
        if cost.complete:
            headline = f"landed {landed_cost.fmt(cost.total_cents)}"
            if cost.credit_pct:
                headline += (f" (net {landed_cost.fmt(cost.net_after_credit_cents)}"
                             f" if {cost.credit_pct}% credit is earned)")
        else:
            headline = (f"at least {landed_cost.fmt(cost.known_minimum_cents)} — "
                        f"INCOMPLETE, {len(cost.undisclosed)} cost(s) undisclosed")
            if cost.credit_pct:
                headline += f", {cost.credit_pct}% credit offered"
        m.best_current_offer = f"{best['company']} — {best['offer_type']} — {headline}"
        m.best_offer_detail = {"company": best["company"],
                               "offer_type": best["offer_type"],
                               "breakdown": cost.render(),
                               "complete": cost.complete,
                               "undisclosed": cost.undisclosed}
        m.estimated_landed_cost = (landed_cost.fmt(cost.total_cents) if cost.complete
                                   else f"{landed_cost.fmt(cost.known_minimum_cents)}+ "
                                        f"(incomplete: {', '.join(cost.undisclosed)})")

    m.zero_dollar = [dict(row) for row in db.rows(
        conn, "SELECT * FROM zero_dollar_opportunities"
              " ORDER BY COALESCE(p_free, 0) + COALESCE(p_credited, 0) DESC,"
              "          score DESC LIMIT 25")]
    m.human_queue = [dict(row) for row in db.rows(
        conn, "SELECT * FROM human_queue ORDER BY priority, kind LIMIT 25")]
    m.top_targets = [dict(row) for row in db.rows(
        conn, "SELECT t.id, c.name AS company, c.company_type, c.state, t.score,"
              "       t.stage, t.strategy, t.p_free_unit, t.p_credited_unit"
              " FROM targets t JOIN companies c ON c.id = t.company_id"
              " WHERE t.stage NOT IN ('lost','disqualified')"
              " ORDER BY t.score DESC NULLS LAST LIMIT 15")]

    m.next_action = _next_action(conn, m)
    return m


def _next_action(conn: sqlite3.Connection, m: Metrics) -> str:
    """One sentence: the single highest-value thing to do right now."""
    if m.profile_gaps:
        return (f"Fill {len(m.profile_gaps)} required field(s) in profile/business.json — "
                f"starting with {m.profile_gaps[0]}. Outreach is blocked until then.")

    approvals = int(db.scalar(conn, "SELECT COUNT(*) FROM approvals WHERE decision IS NULL"))
    if approvals:
        return (f"{approvals} item(s) awaiting your APPROVE SEND / EDIT / CANCEL — "
                "run `da approvals`.")

    blocked = db.one(conn, "SELECT title FROM tasks WHERE kind = 'blocked'"
                           " AND status = 'open' ORDER BY priority LIMIT 1")
    if blocked:
        return f"Blocked: {blocked['title']}"

    free_offer = db.one(conn, "SELECT c.name FROM offers o JOIN targets t ON t.id = o.target_id"
                              " JOIN companies c ON c.id = t.company_id"
                              " WHERE o.landed_cost_cents = 0 AND o.status = 'open' LIMIT 1")
    if free_offer:
        return (f"$0 landed offer open from {free_offer['name']} — confirm the terms in "
                "writing, then decide whether to accept.")

    if m.total_targets == 0:
        return "No targets yet. Run /dealer-research to build the target list."
    if m.researched < m.total_targets:
        return (f"{m.total_targets - m.researched} target(s) unscored — "
                "run `da score` after gathering evidence.")
    if m.contacted == 0:
        return "Targets are scored but nobody has been contacted. Run /prepare-outreach."

    ready = db.one(conn, "SELECT id, program_name FROM applications"
                         " WHERE status = 'ready_for_approval' LIMIT 1")
    if ready:
        return f"Application #{ready['id']} ({ready['program_name']}) is ready for review."

    unhandled = int(db.scalar(conn, "SELECT COUNT(*) FROM responses WHERE handled = 0"))
    if unhandled:
        return f"{unhandled} unhandled response(s) — run /review-responses."

    from . import followups
    due_now = len(followups.due(conn))
    if due_now:
        return f"{due_now} follow-up(s) due — run /followups."

    return "Campaign is idle. Widen discovery or revisit stalled targets."


# ------------------------------------------------------------------ render
def render_terminal(m: Metrics) -> str:
    bar = "=" * 78
    lines = [bar, "DEALER ARBITRAGE — CAMPAIGN STATUS".center(78), bar]

    pairs = [("TOTAL TARGETS", m.total_targets), ("RESEARCHED", m.researched),
             ("CONTACTED", m.contacted), ("APPLICATIONS READY", m.applications_ready),
             ("APPLICATIONS SUBMITTED", m.applications_submitted),
             ("RESPONSES", m.responses), ("NEGOTIATIONS", m.negotiations),
             ("FREE-UNIT OFFERS", m.free_unit_offers),
             ("CREDITED-UNIT OFFERS", m.credited_unit_offers),
             ("PAID OFFERS", m.paid_offers), ("REJECTIONS", m.rejections)]
    for i in range(0, len(pairs), 2):
        chunk = pairs[i:i + 2]
        lines.append("".join(f"  {label:<26}{value:<11}" for label, value in chunk))

    lines += ["", f"  ESTIMATED LANDED COST     {m.estimated_landed_cost}",
              f"  BEST CURRENT OFFER        {m.best_current_offer}",
              f"  NEXT ACTION               {m.next_action}"]

    if m.profile_gaps:
        lines += ["", "-" * 78, "  PROFILE GAPS (blocking):"]
        lines += [f"    - {gap}" for gap in m.profile_gaps[:12]]

    lines += ["", "-" * 78, "  ZERO-DOLLAR OPPORTUNITIES"]
    if m.zero_dollar:
        lines.append(f"    {'COMPANY':<30}{'TYPE':<14}{'ST':<4}{'SCORE':<7}"
                     f"{'FREE%':<7}{'CRED%':<7}STAGE")
        for row in m.zero_dollar[:15]:
            lines.append(
                f"    {str(row['company'])[:29]:<30}{str(row['type'])[:13]:<14}"
                f"{str(row['state'] or '--'):<4}{str(row['score'] or 0):<7}"
                f"{str(row['p_free'] or 0):<7}{str(row['p_credited'] or 0):<7}"
                f"{row['stage']}")
    else:
        lines.append("    (none yet — evidence of a demo/dealer program is what "
                     "qualifies a target here)")

    if m.human_queue:
        lines += ["", "-" * 78, "  WAITING ON YOU"]
        for row in m.human_queue[:10]:
            lines.append(f"    [{row['kind']}] {row['label']}  <- {row['reason']}")

    if m.top_targets:
        lines += ["", "-" * 78, "  TOP TARGETS"]
        lines.append(f"    {'#':<5}{'COMPANY':<30}{'SCORE':<7}{'STAGE':<20}STRATEGY")
        for row in m.top_targets[:10]:
            lines.append(f"    {row['id']:<5}{str(row['company'])[:29]:<30}"
                         f"{str(row['score'] or 0):<7}{str(row['stage'])[:19]:<20}"
                         f"{row['strategy'] or '-'}")

    lines += ["", bar]
    return "\n".join(lines)


def render_html(m: Metrics) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    tiles = [("Total targets", m.total_targets), ("Researched", m.researched),
             ("Contacted", m.contacted), ("Applications ready", m.applications_ready),
             ("Applications submitted", m.applications_submitted),
             ("Responses", m.responses), ("Negotiations", m.negotiations),
             ("Free-unit offers", m.free_unit_offers),
             ("Credited-unit offers", m.credited_unit_offers),
             ("Paid offers", m.paid_offers), ("Rejections", m.rejections)]

    tile_html = "".join(
        f'<div class="tile"><div class="n">{esc(v)}</div>'
        f'<div class="l">{esc(k)}</div></div>' for k, v in tiles)

    zero_rows = "".join(
        f"<tr><td>{esc(r['company'])}</td><td>{esc(r['type'])}</td>"
        f"<td>{esc(r['state'])}</td><td class=num>{esc(r['score'])}</td>"
        f"<td class=num>{esc(r['p_free'])}%</td><td class=num>{esc(r['p_credited'])}%</td>"
        f"<td>{esc(r['strategy'])}</td><td>{esc(r['stage'])}</td></tr>"
        for r in m.zero_dollar) or '<tr><td colspan="8" class="empty">No qualifying '\
        'prospects yet — a target reaches this view on evidence of a demo or dealer '\
        'program, or a scored likelihood of a free or credited unit.</td></tr>'

    top_rows = "".join(
        f"<tr><td class=num>{esc(r['id'])}</td><td>{esc(r['company'])}</td>"
        f"<td>{esc(r['company_type'])}</td><td class=num>{esc(r['score'])}</td>"
        f"<td>{esc(r['stage'])}</td><td>{esc(r['strategy'])}</td></tr>"
        for r in m.top_targets) or '<tr><td colspan="6" class="empty">No targets yet.</td></tr>'

    queue_rows = "".join(
        f"<li><span class=kind>{esc(r['kind'])}</span> {esc(r['label'])} "
        f"<span class=reason>{esc(r['reason'])}</span></li>"
        for r in m.human_queue) or "<li class=empty>Nothing waiting on you.</li>"

    gaps = "".join(f"<li>{esc(g)}</li>" for g in m.profile_gaps)
    gaps_block = (f'<section class="warn"><h2>Profile gaps — outreach is blocked</h2>'
                  f'<ul>{gaps}</ul></section>') if m.profile_gaps else ""

    breakdown = m.best_offer_detail.get("breakdown", "")
    breakdown_block = (f'<pre class="cost">{esc(breakdown)}</pre>' if breakdown else "")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dealer Arbitrage — Campaign Status</title>
<style>
:root {{ --bg:#faf9f7; --fg:#1c1b19; --muted:#6b6862; --line:#e3e0da; --card:#fff;
         --accent:#1f6f4a; --warn:#8a4b1e; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#16151a; --fg:#eceae6; --muted:#9b978f; --line:#2c2a30; --card:#1e1d23;
           --accent:#5fbf8f; --warn:#d69054; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
        font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin:0 0 .25rem; letter-spacing:-.01em; }}
.sub {{ color:var(--muted); margin:0 0 2rem; font-size:.9rem; }}
h2 {{ font-size:1rem; text-transform:uppercase; letter-spacing:.07em;
      color:var(--muted); margin:2.5rem 0 .75rem; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
          gap:.75rem; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
         padding:.9rem 1rem; }}
.tile .n {{ font-size:1.7rem; font-weight:600; line-height:1.1; }}
.tile .l {{ color:var(--muted); font-size:.78rem; text-transform:uppercase;
            letter-spacing:.05em; margin-top:.2rem; }}
.headline {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
             padding:1.1rem 1.25rem; margin-top:1rem; }}
.headline dt {{ color:var(--muted); font-size:.75rem; text-transform:uppercase;
                letter-spacing:.06em; }}
.headline dd {{ margin:.15rem 0 1rem; font-size:1.02rem; }}
.headline dd:last-child {{ margin-bottom:0; }}
.accent {{ color:var(--accent); font-weight:600; }}
table {{ width:100%; border-collapse:collapse; background:var(--card);
         border:1px solid var(--line); border-radius:10px; overflow:hidden;
         font-size:.9rem; }}
.scroll {{ overflow-x:auto; }}
th {{ text-align:left; font-size:.72rem; text-transform:uppercase; letter-spacing:.06em;
      color:var(--muted); padding:.6rem .8rem; border-bottom:1px solid var(--line); }}
td {{ padding:.55rem .8rem; border-bottom:1px solid var(--line); }}
tr:last-child td {{ border-bottom:none; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.empty {{ color:var(--muted); font-style:italic; }}
ul {{ list-style:none; padding:0; margin:0; }}
li {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
      padding:.6rem .8rem; margin-bottom:.4rem; font-size:.9rem; }}
.kind {{ display:inline-block; background:var(--line); border-radius:4px;
         padding:.05rem .4rem; font-size:.72rem; text-transform:uppercase;
         letter-spacing:.05em; margin-right:.5rem; }}
.reason {{ color:var(--muted); font-size:.82rem; }}
.warn h2 {{ color:var(--warn); }}
.warn li {{ border-left:3px solid var(--warn); }}
pre.cost {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
            padding:1rem; overflow-x:auto; font-size:.82rem; margin-top:.75rem; }}
footer {{ color:var(--muted); font-size:.78rem; margin-top:3rem;
          border-top:1px solid var(--line); padding-top:1rem; }}
</style></head><body><div class="wrap">
<h1>Dealer Arbitrage — Campaign Status</h1>
<p class="sub">Objective: one evaluation unit at $0 landed cost. Generated {generated}.</p>

<div class="tiles">{tile_html}</div>

<div class="headline"><dl>
<dt>Estimated landed cost</dt><dd class="accent">{esc(m.estimated_landed_cost)}</dd>
<dt>Best current offer</dt><dd>{esc(m.best_current_offer)}</dd>
<dt>Next action</dt><dd>{esc(m.next_action)}</dd>
</dl>{breakdown_block}</div>

{gaps_block}

<h2>Zero-dollar opportunities</h2>
<div class="scroll"><table>
<tr><th>Company</th><th>Type</th><th>St</th><th>Score</th><th>Free</th>
<th>Credited</th><th>Strategy</th><th>Stage</th></tr>
{zero_rows}</table></div>

<h2>Waiting on you</h2>
<ul>{queue_rows}</ul>

<h2>Top targets</h2>
<div class="scroll"><table>
<tr><th>#</th><th>Company</th><th>Type</th><th>Score</th><th>Stage</th><th>Strategy</th></tr>
{top_rows}</table></div>

<footer>Probabilities are internal priors for sequencing work — not predictions, and
never to be quoted to a counterparty. Every figure here traces to a row in
db/arbitrage.db; every external action is in audit_log.</footer>
</div></body></html>"""


def write_html(m: Metrics, path: Path | None = None) -> Path:
    paths.ensure_dirs()
    path = path or (paths.EXPORT_DIR / "dashboard.html")
    path.write_text(render_html(m), encoding="utf-8")
    return path


def serve(path: Path, port: int = 8765) -> None:
    """Serve the exports directory on localhost — local only, never bound publicly."""
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(path.parent))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Dashboard: http://127.0.0.1:{port}/{path.name}  (ctrl-c to stop)")
        httpd.serve_forever()
