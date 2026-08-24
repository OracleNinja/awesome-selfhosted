# Operating notes

## The daily loop

```bash
./da status          # where things stand, and the single next action
./da tasks           # open work, including evidence gaps worth closing
./da approvals list  # anything waiting on you
./da followups list  # what is due
```

## Approving and sending

The system never sends. It drafts, presents, and records. The sequence:

```bash
./da outreach 12 --present                              # draft + approval block
./da approvals approve --approval-id 5 --by "Your Name" # your decision, recorded
#   ... you send the message yourself ...
./da approvals sent --outreach-id 12 --by "Your Name"   # logs it, starts the ladder
```

`EDIT` returns the draft for revision; `CANCEL` closes it. Every decision is attributed
and appended to `audit_log`, which cannot be edited or deleted.

## Recording an offer

Use `null` — or simply omit — anything the counterparty has not stated. Do not enter 0
to mean "they didn't say."

```bash
./da offer 3 '{"vehicle_price_cents": 645000, "freight_cents": "$950",
               "setup_prep_cents": 0, "credit_pct": 100, "min_order_units": 5,
               "credit_conditions": "within 90 days"}'
```

Money accepts `"$1,250.00"`, `"free"`, `"included"`, `"waived"` → 0, and
`"TBD"`/`"unknown"` → unknown.

## What to get in writing on any demo unit

Before anything is agreed, these need to be explicit:

- Who pays freight, and who books the carrier
- Setup / PDI / assembly responsibility
- Destination charge, doc fee, dealer fees, taxes
- Any required purchase, accessory order, or stocking minimum
- How long the unit stays, and on what terms
- Whether it must be returned, bought, or may be retailed
- Insurance requirements and who carries it
- Title and registration handling
- For a credited demo: credit %, qualifying order size, expiry, whether freight is
  credited, and what happens if the first order comes in smaller than projected

## Browser automation

Off by default per domain. Add a host to
`config/settings.json → security.browser_automation.allowed_domains` before the agent
will drive it. Playwright is optional; without it the system emits an operator script
to `var/exports/operator-script.txt` for a human or for Claude driving Chrome.

The agent stops at any login, CAPTCHA, identity check, payment step or signature. That
is not a failure mode to work around — it is the intended behavior, and the task it
raises tells you what to do at the keyboard.

## When the system refuses something

Read the refusal. Every one names its cause and its remedy:

- *"only 0 evidenced fact(s)"* → research the target; do not lower the threshold
- *"business.json → X is null"* → fill X truthfully, or drop the claim
- *"no APPROVE SEND decision"* → the gate is working
- *"an undisclosed component is not $0"* → go ask for the missing number
- *"claim guard blocked"* → the draft asserts something unsupported; rewrite it

Editing configs to silence a refusal converts a safety property into a liability. If a
guard is genuinely wrong, fix the evidence or the profile — not the guard.

## Backups

`db/arbitrage.db` holds the entire campaign and is gitignored. It is a single SQLite
file: copy it. `var/` holds drafts, screenshots, evidence snapshots and exports, and is
also gitignored — preserve it alongside the database, since it contains the copies of
submitted applications and sent messages.
