# Architecture

## The shape of it

```
  discovery ──► companies/targets ──► scoring ──► strategy
   (Stage 2)      + sources           (Stage 3)   (Stage 4)
                  (Stage 13)                          │
                                                      ▼
  followups ◄── approval gate ◄── outreach ◄──────────┘
  (Stage 10)      (Stage 7)       (Stage 5)
       │              │                │
       │              ▼                ▼
       │        applications      responses ──► offers ──► negotiation
       │          (Stage 6)        (Stage 8)               (Stage 9)
       │                                │                      │
       └────────────────────────────────┴──────────────────────┘
                                        ▼
                              dashboard (Stage 12)
                    every arrow also writes to audit_log (Stage 11)
```

## Module responsibilities

| Module | Owns |
|---|---|
| `paths` | Filesystem layout; `DEALER_ARBITRAGE_HOME` override for testing |
| `db` | Connection, schema application, `audit()`, row helpers, company merge |
| `profile` | Stage 1 — the only source of facts about the business; requirement levels |
| `evidence` | Stage 13 — the source store and the claim guard (both directions) |
| `discovery` | Stage 2 — query plan and finding intake with evidence enforcement |
| `scoring` | Stage 3 — config-driven 0-100 model; suppresses unevidenced signals |
| `strategy` | Stage 4 — ranked acquisition strategies and the concession ladder |
| `landed_cost` | The cost model; unknown ≠ zero |
| `outreach` | Stage 5 — template rendering, personalization threshold, claim guard |
| `approval` | Stage 7 — the gate; the only path to a recorded send |
| `applications` | Stage 6 — form mapping, browser adapters, READY FOR APPROVAL report |
| `responses` | Stage 8 — rule-based classification with visible cues |
| `negotiation` | Stage 9 — landed-cost optimization, ladder walking |
| `followups` | Stage 10 — the 3/7/14/30 ladder with angle rotation |
| `dashboard` | Stage 12 — terminal and HTML views |
| `security` | Stage 14 — walls, secrets, allowlists, fraud markers |
| `cli` | Stage 15 — argparse surface behind the slash commands |

## Why the guarantees are layered

Each critical refusal is enforced in more than one place, because a single check is a
single point of failure:

**Sending outreach** — `approval.record_sent()` verifies an `APPROVE SEND` row exists;
independently, a SQLite trigger refuses to set `outreach.status = 'sent'` without one.
A future code path that forgets the check still cannot write the row.

**Submitting an application** — `applications.submit()` raises; the Playwright adapter
has no submit method at all (absent, not disabled); `record_submission()` requires a
prior approval; a trigger requires `approved_by`/`approved_at`.

**The audit trail** — triggers block `UPDATE` and `DELETE` on `audit_log` entirely.

**Claims** — the profile refuses to invent, the intake refuses to store unevidenced
status, the claim guard refuses to render, and the approval block re-checks evidence
for every asserted claim before a human sees it.

## Design decisions worth knowing

**Money is integer cents, and `NULL` means unknown.** Not zero. `landed_cost.compute()`
returns `total_cents = None` when any required component is undisclosed, and
`rank_key()` sorts incomplete quotes below complete ones regardless of how attractive
the disclosed part looks.

**Evidence gates scoring, not just prose.** A positive signal marked
`requires_evidence` contributes zero until a matching `sources` row exists. When a
heavyweight signal is suppressed, the scorer raises a task naming the claim to go
collect — the gap becomes work, not a dead end.

**Company merges never overwrite.** `upsert_company` fills nulls but leaves conflicting
non-null values alone and logs the conflict. Two sources disagreeing is a research
question for a human, not something to silently resolve.

**A price outranks promotional language.** "Free demo — the unit is $6,450" classifies
as a paid offer. The bias is deliberately conservative: mislabeling a real gift costs
one human glance; the reverse could walk a cost into the campaign unnoticed.

**Opt-out closes the company, not the address.** An opted-out named contact does not
leave the general inbox open.

**Templates use a hand-written front-matter parser.** The grammar is fixed and the
templates are ours; a parser we control cannot surprise us on someone else's input, and
it keeps the core dependency-free.
