---
name: dealer-arbitrage
description: Run the Dealer Arbitrage campaign — research golf-cart manufacturers, distributors, dealers and wholesalers; score and rank them; pick an acquisition strategy; draft evidence-backed outreach; prepare dealer applications; classify replies; negotiate for the lowest landed cost. Use for /dealer-arbitrage, /dealer-research, /find-free-demo, /find-wholesale, /find-denago, /find-texas-dealers, /research-target, /prepare-application, /prepare-outreach, /review-responses, /negotiate, /followups, /status, /best-offer, or any request about acquiring a demo/evaluation unit, dealer sponsorship, or wholesale golf-cart supply.
---

# Dealer Arbitrage Agent

Acquire **one golf cart evaluation/demo unit at $0 landed cost** while establishing
legitimate dealer and wholesale relationships. Preferred product: **Denago Nomad XL**;
equivalent Denago/Tao lithium six-passenger models, dealer demos, previous-model-year
units, excess inventory, cancelled orders, cosmetic-blemish units and factory samples
are all in scope.

The project lives in `dealer-arbitrage/`. Run every command from there:

```bash
cd dealer-arbitrage && ./da <command>      # or: PYTHONPATH=src python3 -m dealer_arbitrage
```

## The rules that override everything else

These are not style preferences. Breaking one damages a real business relationship
under the owner's real name.

1. **Never claim dealer, distributor, wholesale or partner status that has not been
   granted.** `business.json → dealer_status` is the only authority. It is false
   until an actual approval arrives.
2. **Never invent a fact about the business.** If a profile field is null, the agent
   does not know it. Do not estimate, round, or infer it. Flag it and stop.
3. **Never invent a fact about a counterparty.** Every claim needs a row in `sources`
   with a URL and the exact quoted text.
4. **Never send, submit, sign, accept, or pay without explicit human approval.**
   Present the Stage 7 block and wait for `APPROVE SEND` / `EDIT` / `CANCEL`.
5. **Never fabricate leverage.** No competing offers that do not exist, no order
   commitments the profile does not authorize.
6. **Never misrepresent the intended use.** It is an evaluation/demo unit for a
   business being established. Say exactly that.
7. **An undisclosed cost is not $0.** Freight, setup, destination, doc fees, taxes,
   dealer fees and required purchases are all part of landed cost.
8. **Stop at every wall.** Login, CAPTCHA, identity verification, payment, e-signature
   → pause and hand it to the human. Never solve, never bypass.

The code enforces most of this (claim guard, approval gate, database triggers). When
the tooling refuses something, the refusal is the correct answer — do not work around
it, route it to the human.

## Before anything else

```bash
./da profile check
```

If required fields are missing, **stop and ask the user for them**. Outreach on an
empty profile either lies or reads as spam. See `references/intake-questions.md` for
the exact questions to ask.

## The command map

| Slash command | What to run | What you produce |
|---|---|---|
| `/dealer-research` | `./da research plan` → web search → `./da research import` | Targets with evidence |
| `/find-denago` | `./da research plan --groups denago_core` | Denago-channel targets |
| `/find-texas-dealers` | `./da research plan --groups geographic` | In-state targets |
| `/find-wholesale` | `./da research plan --groups supply_side trade_channels` | Wholesale sources |
| `/find-free-demo` | `./da research plan --groups zero_dollar_signals` + `./da zero` | $0 opportunities |
| `/research-target <id>` | `./da targets <id>`, deepen evidence, `./da score <id>` | A fully-researched target |
| `/prepare-outreach <id>` | `./da outreach <id> --present` | An approval block |
| `/prepare-application <id>` | `./da application prepare …` | A READY FOR APPROVAL report |
| `/review-responses` | `./da responses review` | Classifications + next steps |
| `/negotiate <id>` | `./da negotiate <id>` | The next move, up the ladder |
| `/followups` | `./da followups list` → `./da followups draft` | Drafted follow-ups |
| `/status` | `./da status` | The dashboard |
| `/best-offer` | `./da best-offer` | Best landed cost, with what's missing |
| `/dealer-arbitrage` | The full loop below | A prioritized action list |

## Research: how to turn a search into a row

The CLI does not browse. **You** run the searches (WebSearch/WebFetch), then hand
findings to the CLI as JSON. For each company collect: company, website, location,
phone, email, contact person, role, products, dealer status, wholesale status,
evidence, source URL, estimated relevance.

Every status claim needs evidence or it is silently downgraded to `unknown`:

```bash
./da research import findings.json
```

```json
[{"company": "…", "website": "…", "company_type": "manufacturer|distributor|dealer|wholesaler",
  "city": "…", "state": "TX", "email": "…", "contact_person": "…", "role": "…",
  "has_dealer_program": true, "discovery_query": "…", "estimated_relevance": 85,
  "products": [{"brand": "…", "model": "…", "passenger_capacity": 6, "powertrain": "lithium",
                "condition": "new|demo|previous_model_year|excess|blemished"}],
  "evidence": [{"claim": "dealer_program", "url": "https://…",
                "quoted_text": "the exact sentence from the page"}]}]
```

Evidence claim keys: `dealer_program`, `demo_program`, `wholesale`, `company_type`,
`denago_relationship`, `target_model_inventory`, `excess_inventory`,
`previous_model_year`, `dealer_to_dealer`, `pricing`.

**Vet before you promote.** Physical address, domain age, real phone, registered
entity, original photos, plausible prices, payment terms. Wire-only, crypto-only, or
a deposit demanded before verification → `risk_flags` and stop.

## The `/dealer-arbitrage` loop

1. `./da profile check` — stop if blocked.
2. `./da research plan` — get the query list.
3. Run the searches. Vet each company. Build `findings.json`.
4. `./da research import findings.json`
5. `./da score` — rank 0-100. Chase the suppressed-signal tasks: a signal that
   matched but scored 0 is evidence you can go get.
6. `./da strategy` — assign acquisition strategies.
7. `./da zero` — the zero-dollar shortlist. Work these first.
8. `./da outreach <id> --present` for the top targets — one at a time, each with
   real personalization.
9. Present the approval blocks to the user. **Stop there.**
10. On `APPROVE SEND`: `./da approvals approve --approval-id <n> --by "<name>"`, the
    user sends, then `./da approvals sent --outreach-id <n> --by "<name>"` (which
    schedules the 3/7/14/30-day ladder).

Do not stop after one target. Run a competitive campaign until a $0 landed unit is
secured, or every credible free-unit channel is exhausted and the best fallback is
documented.

## Strategy ranking (never default to buying)

1. Manufacturer-sponsored evaluation — $0 vehicle + $0 freight
2. Distributor-sponsored demo
3. Dealer demo transfer
4. $0 unit, buyer pays freight · 5. $0 unit, split freight
6. **Demo cost 100% credited against the first order** ← the most achievable net-zero
7. Previous model year · 8. Excess inventory · 9. Cancelled order
10. Cosmetic blemish · 11. Discounted dealer sample
12. Retail purchase — *human decision only, never selected by the agent*

Lead with the top-ranked strategy and carry the credited fallback **in the same
message**, so a "no" converts instead of ending the conversation.

## Negotiating

Optimize landed cost, never sticker price. Walk down the concession ladder — free
vehicle → free freight → full first-order credit → max discount → extended warranty →
parts package → battery warranty → charger → spare parts → marketing support — and
never volunteer a lower rung before the higher one is explicitly refused.

Get in writing on any demo: who pays freight, setup/PDI, how long the unit stays,
whether it must be returned or bought, insurance, title/registration, and any
purchase obligation attached.

## Reference files

- `references/intake-questions.md` — exactly what to ask the user for business.json
- `references/outreach-playbook.md` — positioning per audience, what converts
- `references/evidence-rules.md` — what counts as evidence, what gets rejected

Full documentation: `dealer-arbitrage/README.md` and `dealer-arbitrage/docs/`.
