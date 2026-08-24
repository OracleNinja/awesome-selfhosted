# Dealer Arbitrage Agent

An assisted-acquisition system for obtaining a manufacturer-, distributor-, or
dealer-sponsored **golf cart evaluation unit at $0 landed cost**, while establishing
legitimate wholesale and dealer relationships.

Preferred product: **Denago Nomad XL**. Also in scope: equivalent Denago/Tao lithium
six-passenger models, dealer demos, previous-model-year units, excess inventory,
customer cancellations, cosmetic-blemish units, promotional units and factory samples.

This is a business-development tool, not a shopping assistant. It researches supply
channels, ranks them, drafts honest outreach, prepares dealer applications, tracks
every response, and negotiates toward the lowest total acquisition cost.

## What it will not do

The interesting part of this system is what it refuses. These are enforced in code and
in the database schema, not just written down:

| Refusal | Where it lives |
|---|---|
| Claim dealer/distributor/wholesale status not actually granted | `evidence.ClaimGuard` |
| State any fact about the business that `business.json` does not contain | `profile.BusinessProfile` |
| State any fact about a counterparty without a stored URL + quotation | `evidence` + `discovery.intake` |
| Send outreach without a recorded `APPROVE SEND` | `approval` **and** a DB trigger |
| Submit an application | `applications.submit()` raises; the browser adapter has no submit method |
| Accept an offer or make a purchase | `negotiation.accept_offer()`, `approval.purchase_gate()` raise |
| Treat an undisclosed cost as $0 | `landed_cost` |
| Fabricate a competing offer or a commitment | `evidence.ClaimGuard` |
| Solve a CAPTCHA, log in, verify identity, or authorize payment | `security` |
| Edit or delete the audit trail | DB triggers |

Autonomous sending requires **two** switches: a config flag *and* the specific company
on an allowlist. Purchases, offer acceptance and application submission cannot be
made autonomous at all.

## Quick start

```bash
cd dealer-arbitrage
./da init                 # create db/arbitrage.db
./da profile check        # see exactly what business.json still needs
```

Fill in `profile/business.json`. Sensitive identifiers (EIN, permit numbers) go in
`profile/sensitive.local.json` — copy the `.example` file; it is gitignored. Set
`campaign.home_market` in `config/settings.json`.

Then run the campaign through the Claude Code skill (`/dealer-arbitrage`) or by hand:

```bash
./da research plan                     # the query list to run
./da research import findings.json     # intake, with evidence
./da score                             # rank 0-100
./da strategy                          # assign acquisition strategies
./da zero                              # the zero-dollar shortlist
./da outreach 1 --present              # draft + approval block, then STOP
./da status                            # the dashboard
```

## Commands

| Command | Purpose |
|---|---|
| `init` | Create/upgrade the database |
| `profile check\|show\|requirements` | What the agent knows, and what is missing |
| `research plan\|import\|add` | The discovery plan and finding intake |
| `evidence add\|list` | Store and review source evidence |
| `score [id]` | Score targets 0-100 |
| `strategy [id]` | Assign acquisition strategies |
| `outreach <id> [--present]` | Draft personalized outreach |
| `approvals list\|present\|approve\|edit\|cancel\|sent` | The approval gate |
| `application prepare\|screenshot\|submitted` | Dealer applications |
| `responses add\|review\|handled` | Inbound classification |
| `offer <response_id> <json>` | Record a commercial offer |
| `negotiate <id> [--record]` | The next negotiation move |
| `followups list\|draft\|stop` | The 3/7/14/30-day ladder |
| `status [--html] [--serve]` | Campaign dashboard |
| `zero` / `best-offer` / `targets` / `tasks` / `audit` | Views |

## Layout

```
config/     settings, scoring weights, strategies, search queries, profile requirements
db/         schema.sql + arbitrage.db (gitignored)
profile/    business.json, sensitive.local.json (gitignored), documents/, photos/
templates/  outreach/ (4 audiences), followup/ (4 angles)
src/        the package
tests/      102 tests, most of them about the refusals
var/        drafts, screenshots, evidence snapshots, exports (all gitignored)
docs/       architecture and operating notes
```

Claude Code integration lives at the repo root: `.claude/skills/dealer-arbitrage/`
and `.claude/commands/` (14 slash commands).

## Landed cost, not sticker price

A "free" cart with $950 freight, $300 setup, a $200 doc fee and a required $1,200
accessory order is a $2,650 cart. The system computes landed cost from every component
and **refuses to total a quote with undisclosed components** — it reports what is
disclosed, names what is missing, and sorts incomplete quotes below complete ones.

A 100%-credited demo is reported as *conditional*: what is owed today is shown next to
the net, along with the qualifying order size and expiry.

## Tests

```bash
PYTHONPATH=src python3 -m pytest tests/ -q
```

No third-party dependencies for the core. Playwright is optional (`pip install
'.[browser]'`) and only enables form inspection and screenshots — never submission.

## Honest limitations

- **Probabilities are priors, not predictions.** `p_free_unit` etc. exist to sequence
  work. They are not to be quoted to anyone.
- **The CLI does not browse.** Searching is done by the agent or a human; the CLI owns
  the plan, the evidence rules, and the intake.
- **A $0 unit is genuinely rare.** The credited-demo structure is the achievable
  version of the same outcome and usually the better thing to pursue.
- **Nothing here makes an unqualified business qualified.** If the profile is thin, the
  honest message is a thin message. The system's job is to make it truthful and
  well-targeted, not impressive.
