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

Seven agents, in `.claude/agents/`. Responsibilities do not overlap, and the
handoff between any two is a file plus a passing gate — never a conversation.

| Agent | Owns | Hands off |
|---|---|---|
| `kdp-orchestrator` | Routing and stage advancement | The only agent that may advance a book |
| `kdp-market-researcher` | Abstracted market signals | `research.json` → concept |
| `kdp-concept-designer` | The `BookSpec` | `manifest.json` → generation |
| `kdp-asset-generator` | Art and text assets, with provenance | assets + provenance → QA |
| `kdp-qa-auditor` | Originality and quality findings | A verdict → production, or back to generation |
| `kdp-production-engineer` | Interior and cover PDFs | Built PDFs → publishing prep |
| `kdp-publishing-preparer` | Listing metadata and the disclosure answer | A checklist → a human |

Two separations are load-bearing:

- **The auditor cannot fix what it finds.** It has read-only tools. An auditor
  that repairs its own findings has an interest in finding fewer of them.
- **The orchestrator cannot judge.** It routes and reads exit codes. It cannot
  decide a failure looks cosmetic, because that decision is where quality
  systems rot.

## Stages, gates, and data flow

```
  research ──G1──▶ concept ──G2──▶ generation ──G3──▶ qa
                                                       │
                                                      G4
                                                       ▼
   ready_for_submission ◀──G6── publishing_prep ◀──G5── production
            │
            └─▶ a human fills in the KDP form
```

| Gate | Stage | Refuses |
|---|---|---|
| G1 research hygiene | research | Long free text, media references, quoted material, no signals |
| G2 spec legality | concept | Unprintable page count, mismatched counts, gaps in page order, duplicate ids, art on a recto in a single-sided book, unverified spec tables |
| G3 provenance completeness | generation | An art page with no record, an unclassified asset, a generated asset with no prompt version |
| G4 originality | qa | Repeated pages, pages reused from a published book; blocks when near-duplicate detection is unavailable |
| G5 print preflight | production | No live area, unsafe spine text, no art pages, missing files; blocks without the PDFs or the PDF toolchain |
| G6 metadata compliance | publishing_prep | Incomplete or unconfirmed disclosure, keyword stuffing, thin description, malformed listing |

Each stage is independently runnable and testable:

```bash
python3 -m kdp specs --trim 8.5x11 --pages 60   # resolved print geometry
python3 -m kdp research   books/b1/research.json
python3 -m kdp concept    books/b1/manifest.json
python3 -m kdp generation books/b1/manifest.json
python3 -m kdp qa         books/b1/manifest.json --catalogue catalogue.json
python3 -m kdp production books/b1/manifest.json --interior i.pdf --cover c.pdf
python3 -m kdp publishing-prep books/b1/manifest.json
python3 -m kdp disclosure books/b1/manifest.json
python3 -m kdp advance    books/b1/manifest.json reports/qa.json
```

Exit codes: `0` passed, `1` stopped, `2` misused.

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

## Implementation plan

**Stage 1 — foundation (done).** Print-spec domain, provenance and disclosure,
manifest, six gates, stage machine, CLI, 108 tests, CI, and the seven agents.
Zero third-party dependencies; the whole suite runs on a bare Python 3.11.

**Stage 2 — verify the spec tables.** `kdp/specs/revision.py` is marked
`UNVERIFIED` and G2 fails while it stays that way. Confirm every number against
KDP Help, then flip it. This is the first thing to do and it is deliberately
impossible to skip silently.

**Stage 3 — imaging (unlocks G4).** Install the imaging extras; add perceptual
hashing for near-duplicates and the line-art quality checks (closed regions,
line weight, ink coverage, stray anti-aliasing). Borrow from
`embroidery-genie-ai`'s raster module.

**Stage 4 — production (unlocks G5).** Interior imposition and cover
construction with `reportlab`; deep preflight with `pypdf` for page geometry,
fonts, DPI and colour space.

**Stage 5 — research fetch.** Wire the `ornith-cloud` retrieval pipeline behind
the researcher agent, keeping the G1 whitelist as the boundary.

**Stage 6 — generation cost control.** Port the operation-registry pattern for
image generation, with a file-backed ledger.
