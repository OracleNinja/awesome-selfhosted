# KDP Studio — architecture

A gated, auditable pipeline for producing **original** low-content books for
Amazon KDP. Nothing here publishes; the pipeline ends one step short of the
KDP form, and a human takes it from there.

## Audit: what already existed

This project was designed after auditing the monorepo, so that it reuses what
is there and duplicates nothing.

### Claude/ECC infrastructure

There was **no repository-level Claude infrastructure at all** — no `.claude/`,
no agents, skills, commands or hooks, no `CLAUDE.md`, and no GitHub Actions
workflows. `.github/` held only an upstream pull-request template and an issue
config, both inherited from the `awesome-selfhosted` fork this repository grew
out of.

What does exist is **user-global**, at `~/.claude`, and is out of scope for
this project: a SessionStart git-identity hook, a Stop git-check hook, and the
synced skills (`pdf`, `docx`, `xlsx`, `pptx`, `canvas-design`, `skill-creator`,
`mcp-builder`, `web-artifacts-builder`, and others). None of it was modified,
and none of it is duplicated here — where this pipeline needs PDF or
spreadsheet work, it uses those skills.

### Engineering worth reusing

| Capability | Where | Reused how |
|---|---|---|
| AI cost-control gateway | `embroidery-genie-ai/backend/app/ai/` | Pattern, not code — see below |
| Export package + manifest | `embroidery-genie-ai/backend/app/embroidery/package.py` | Model for the KDP submission package |
| Raster/vector/PDF layer | `embroidery-genie-ai/backend/app/embroidery/{render,raster,svg_parse,reports}.py` | Line-art rendering and preflight, once G4/G5 gain their deps |
| Retrieval pipeline | `ornith-cloud/src/retrieval/` | Search → fetch → extract → rank → cache, for the research stage |
| Test conventions | `pytest`, `vitest` | Adopted as-is |

### Why the AI gateway is reused as a pattern rather than imported

`embroidery-genie-ai`'s gateway is excellent and does exactly what generation
cost control needs: an operation registry where every AI call is declared in
advance with its token ceilings, timeout, retry budget, failure mode and kill
switch; budgets across five scopes; a content-hash cache keyed partly on
`prompt_version` so editing a prompt invalidates old answers; and one ledger
row per attempt.

It is also bound to SQLAlchemy sessions and a tenant/organization model.
Importing it would mean either dragging a database dependency into a
filesystem-based pipeline, or refactoring `embroidery-genie-ai` to extract a
shared package — and refactoring an unrelated, working project was explicitly
out of scope.

So this project reuses the **contract and the discipline**: assets carry
`tool` and `prompt_version`, generation is declared before it runs, and every
attempt is recorded. The duplication is deliberate and narrow. If a third
project ever needs the same gateway, that is the point to extract a shared
package properly — and this note is here so that decision is made knowingly.

## Design principles

**Gates are deterministic, never model judgement.** Every gate is pure stdlib
Python: same inputs, same verdict, forever. No gate calls a model, the network,
or the clock. A stored gate result can be re-checked months later and will still
mean what it said.

**A gate that cannot run does not pass.** Three statuses — `PASS`, `FAIL`,
`BLOCKED` — and only `PASS` advances a book. `BLOCKED` means the gate could not
be evaluated: a dependency is missing, or evidence was not supplied. The
tempting implementation treats "I could not check" as "nothing found"; that
turns an uninstalled library into a silent quality regression, invisible exactly
when it matters.

**Unknown provenance fails closed.** An unclassified asset never rolls up to
"no AI disclosure required". Over-disclosure costs nothing — KDP states the
answer is internal and affects neither royalties nor ranking. Under-disclosure
risks the title and the account. The asymmetry drives the default.

**Audit before automation.** One canonical JSON manifest per book carries the
spec, every asset's provenance, the metadata, the disclosure roll-up, and the
spec revision the geometry was computed under. Serialisation is canonical, so a
diff always means something really changed.

## Agents

Ten agents, in `.claude/agents/`. Responsibilities do not overlap, and the
handoff between any two is a file plus a passing gate — never a conversation.

| Agent | Owns | Read-only |
|---|---|---|
| `kdp-orchestrator` | Routing and stage advancement | — |
| `kdp-market-researcher` | Abstracted market signals, confidence-labelled | — |
| `kdp-book-strategist` | Audience, unique angle, differentiators, risks | — |
| `kdp-concept-designer` | The `BookSpec` and the page architecture | — |
| `kdp-prompt-planner` | Versioned per-page prompts | — |
| `kdp-asset-generator` | Assets, attempts, and the repair loop | — |
| `kdp-qa-auditor` | Asset QA, originality, interior QA, cover QA | **yes** |
| `kdp-production-engineer` | Interior and cover PDFs | — |
| `kdp-publishing-preparer` | Listing metadata, pricing scenarios | — |
| `kdp-compliance-auditor` | KDP compliance, final audit, review package | **yes** |

### Why only three agents were added

The build brief lists fourteen candidate roles. Most map onto agents that
already existed, and creating a second agent for the same job would have been
duplication of exactly the kind the audit was meant to prevent:

- **Book/page architect** → `kdp-concept-designer` already produces both the
  specification and the page architecture; they are one artefact.
- **Interior QA and cover QA** → `kdp-qa-auditor`. Same read-only posture, same
  skill, different gates. A separate agent would differ only in which command
  it runs.
- **Repair agent** → `kdp-asset-generator`. The repair loop *is* re-running the
  generator: it skips approved and pending pages and regenerates only what QA
  rejected. A separate agent would be the same code with a different name.
- **Cover agent** → `kdp-production-engineer`, which builds both PDFs from the
  same geometry. Splitting them is how the spine ends up computed from a stale
  page count.

Three were genuinely new, each because it is a distinct *decision* rather than
a distinct command:

- **`kdp-book-strategist`** — deciding what to build is not observing the
  market (research) or specifying the book (concept). Skipping it is how a
  pipeline produces competent copies of what already sells.
- **`kdp-prompt-planner`** — one vague prompt reused across a book passes every
  structural check and then produces one drawing twenty times. Catching that
  needs its own gate, which needs its own artefact.
- **`kdp-compliance-auditor`** — read-only, and separate from
  `kdp-publishing-preparer` on purpose. The agent that writes the listing must
  not be the one that certifies it.

Three separations are load-bearing:

- **Auditors cannot fix what they find.** Both have read-only tools. An auditor
  that repairs its own findings has an interest in finding fewer of them.
- **Producers cannot certify their own output.** The generator marks new
  attempts `pending`, never `approved`; the preparer does not run its own
  compliance gate.
- **The orchestrator cannot judge.** It routes and reads exit codes. It cannot
  decide a failure looks cosmetic, because that decision is where quality
  systems rot.

## Stages, gates, and data flow

Eleven working stages plus a terminal one. The build brief's sixteen conceptual
steps map onto them as below; several steps share a stage because they share an
artefact and a gate, and splitting those would add transitions without adding a
decision. The mapping is asserted in `tests/test_pipeline.py`, so it cannot
drift from the code.

```
 research ─G1─▶ strategy ─G7─▶ concept ─G2─▶ planning ─G8─▶ generation
                                                                 │
                                                                G3
                                                                 ▼
   production ◀──── G4, G9 ──── qa ◀────────────────────────────┘
       │
      G5
       ▼
 production_qa ─G10,G11─▶ publishing_prep ─G6,G12─▶ final_audit
                                                        │
                                                       G13
                                                        ▼
   ready_for_submission ◀──── G14 ──── human_approval
            │
            └─▶ a human fills in the KDP form
```

| Conceptual step | Stage | Gates |
|---|---|---|
| 1 market research | `research` | G1 |
| 2 opportunity / strategy | `strategy` | G7 |
| 3 book specification, 4 page architecture | `concept` | G2 |
| 5 prompt / asset plan | `planning` | G8 |
| 6 asset generation | `generation` | G3 |
| 7 asset QA | `qa` | G4, G9 |
| 8 book assembly, 10 cover production | `production` | G5 |
| 9 interior QA, 11 cover QA | `production_qa` | G10, G11 |
| 12 metadata, 13 KDP compliance | `publishing_prep` | G6, G12 |
| 14 final publishing audit | `final_audit` | G13 |
| 15 human approval | `human_approval` | G14 |
| 16 publishing preparation | `ready_for_submission` | — |

| Gate | Refuses |
|---|---|
| G1 research hygiene | Long free text, media references, quoted material, no signals |
| G7 strategy differentiation | No unique angle, fewer than two *concrete* differentiators, unlabelled market claims, no risks named |
| G2 spec legality | Unprintable page count, mismatched counts, gaps in page order, duplicate ids, art on a recto in a single-sided book, unverified spec tables |
| G8 prompt plan | A page with no prompt, a prompt too thin, two pages sharing a prompt, complexity out of step with the spec |
| G3 provenance completeness | An art page with no record, an unclassified shipping asset, a generated asset with no prompt version, an unjudged attempt |
| G4 originality | Repeated pages, pages reused from a published book; blocks without near-duplicate detection |
| G9 asset QA | A page with no approved version, two approved versions, below 300 DPI, wrong prompt; blocks without pixel inspection |
| G5 print preflight | No live area, unsafe spine text, no art pages, missing files; blocks without the PDFs or a PDF reader |
| G10 interior QA | An interior PDF that changed after assembly, missing approved assets; blocks without a PDF reader |
| G11 cover QA | A cover built before the current interior, unsafe spine text, a changed file; blocks without a PDF reader |
| G6 metadata compliance | Incomplete or unconfirmed disclosure, keyword stuffing, thin description, malformed listing |
| G12 KDP compliance | Unsupportable claims, trademark proximity, a listing overstating the book; blocks on unverified policy data |
| G13 final audit | Any page not ready, a missing or altered artefact, an undeterminable disclosure, no price, a price that loses money; blocks on unverified spec tables |
| G14 human approval | Everything, until a person records a decision about this exact book |

`python3 -m kdp gates` prints all fourteen with their purpose and inputs.

Each stage is independently runnable and testable:

```bash
python3 -m kdp specs --trim 8.5x11 --pages 60   # resolved print geometry
python3 -m kdp research        books/b1/research.json
python3 -m kdp strategy        books/b1/manifest.json
python3 -m kdp concept         books/b1/manifest.json
python3 -m kdp planning        books/b1/manifest.json
python3 -m kdp generate        books/b1/manifest.json --provider mock
python3 -m kdp asset           books/b1/manifest.json approve PAGE-004.a001
python3 -m kdp generation      books/b1/manifest.json
python3 -m kdp qa              books/b1/manifest.json --catalogue catalogue.json
python3 -m kdp build           books/b1/manifest.json
python3 -m kdp production      books/b1/manifest.json --interior i.pdf --cover c.pdf
python3 -m kdp production-qa   books/b1/manifest.json
python3 -m kdp publishing-prep books/b1/manifest.json
python3 -m kdp final-audit     books/b1/manifest.json
python3 -m kdp review          books/b1/manifest.json --out review.md
python3 -m kdp approve         books/b1/manifest.json approve --reviewer <name>
python3 -m kdp package         books/b1/manifest.json
python3 -m kdp disclosure      books/b1/manifest.json
python3 -m kdp advance         books/b1/manifest.json reports/qa.json
```

Exit codes: `0` passed, `1` stopped, `2` misused.

## Human approval

The pipeline stops at `human_approval` and cannot get past it on its own. G14
passes only when an `ApprovalRecord` exists whose fingerprint matches the book
as it currently stands.

The fingerprint covers the shipping assets, the built interior and cover, the
listing metadata and the chosen price. Editing any of them after sign-off
invalidates the approval — the gate fails with `approval.stale` and
`kdp package` refuses to build. Adding a run record or a note does not, because
those are not the book.

There is no flag that satisfies G14, and nothing in this package can publish:
`tests/test_end_to_end.py` asserts that no module outside `kdp/providers/`
imports an HTTP client at all.

## Provenance and attempts

`AssetProvenance` records one *version* of one asset. A page that took three
tries keeps all three; shipping versions are a filter (`status == approved`)
rather than a second structure, so the two cannot disagree.

```
PAGE-014.a001  generated → rejected  (stray shading)
PAGE-014.a002  generated → failed    (provider timeout)
PAGE-014.a003  generated → approved  ← ships
```

The generator marks new attempts `pending`. Only QA moves a version to
`approved` or `rejected`, and the generator regenerates a page only when its
last attempt was rejected or failed — an approved page is finished, and a
pending one is work already paid for that QA has not yet looked at.

## Economics

Every figure is a projection carrying a `Confidence`, and the roll-up takes the
*weakest* assumption: a projection is only as sound as its shakiest input.

The royalty rate and printing-cost constants are `UNKNOWN` until the `royalty`
specification table is verified, so today every projection reports as unknown-
confidence. `python3 -m kdp` compares scenarios side by side; no price is
privileged, and whether $5.99 works depends entirely on the page count.

## Higgsfield

Reachable two ways, and they are not interchangeable:

1. **As an MCP tool attached to Claude** — the agent calls it, writes the image
   into the book directory, and records provenance. The pipeline never holds a
   credential. This is the intended workflow.
2. **As a direct HTTP client from this package** — `kdp/providers/higgsfield.py`.

No Higgsfield MCP server was attached when this was built and no public API
contract was available, so the direct client is a boundary rather than an
implementation: it reports itself unavailable and says why. A guessed endpoint
would look like an integration and fail on first contact. Everything around it
— request/result types, provenance, attempts, retries — is provider-agnostic
and already in place; completing it means filling in three functions.

Credentials are read from the environment at call time and never written to a
manifest, a log, or a provenance record. `provider` and `model` name a service;
they are not secrets.

## Directory structure

```
kdp-studio/
├── ARCHITECTURE.md          this document
├── COMPLIANCE.md            KDP policy notes and where each is enforced
├── README.md
├── requirements.txt         core needs none of it; extras unlock G4/G5
├── pytest.ini
├── kdp/
│   ├── specs/               versioned KDP print maths (stdlib only)
│   │   ├── revision.py      SPEC_REVISION + verification state
│   │   ├── paper.py         stocks, caliper, page-count limits
│   │   ├── trim.py          trim sizes
│   │   ├── interior.py      bleed, margins, gutter bands, live area
│   │   └── cover.py         spine width, full-wrap canvas
│   ├── models/
│   │   ├── research.py      MarketSignal whitelist, ResearchBrief
│   │   ├── spec.py          BookSpec, PageSpec, BookMetadata
│   │   ├── provenance.py    AssetProvenance, AIRole, disclosure roll-up
│   │   └── manifest.py      BookManifest, canonical JSON
│   ├── gates/               one module per gate + base types and runner
│   ├── pipeline.py          the stage machine
│   └── cli.py               python -m kdp
├── tests/                   108 tests, stdlib + pytest only
└── books/<book-id>/         per-book working directory
    ├── research.json
    ├── manifest.json        the auditable record
    ├── assets/              generated art (gitignored)
    ├── build/               interior.pdf, cover.pdf (gitignored)
    └── reports/<stage>.json stored gate verdicts
```

## What remains

**Verify the specification tables.** `kdp/specs/revision.py` carries a
`SourceRecord` per table, all `UNVERIFIED`, because kdp.amazon.com was
unreachable from the environment this was built in. G2, G12 and G13 refuse to
certify a book while that stands. Read the primary KDP Help pages, confirm each
table, record the URL and date. Verifications expire after 180 days.

One value is known to be contested: secondary sources disagree on the page
count above which KDP prints spine text (79 vs 100). 100 is encoded as the
conservative choice.

**Pixel inspection (G4, G9).** With Pillow installed the gates run, but the
pixel checks themselves — stray shading, unclosed regions, watermarks,
near-duplicates — are still to be implemented. `embroidery-genie-ai`'s raster
module is the place to borrow from.

**Deep PDF inspection (G5, G10, G11).** With pypdf installed the gates run;
the checks that read page geometry, fonts and embedded image DPI out of the
built PDFs are still to be written.

**The Higgsfield HTTP client.** See above — a boundary, pending an API
contract.

**Research fetch.** Wire `ornith-cloud`'s retrieval pipeline behind the
researcher agent, keeping the G1 whitelist as the boundary.

**Generation cost control.** Port the operation-registry pattern from
`embroidery-genie-ai`'s AI gateway, with a file-backed ledger, once a metered
provider is actually in use.
