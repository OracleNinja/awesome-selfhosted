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
| G4 originality | Repeated pages, rescaled or mirrored repeats, pages reused from a published book; blocks for any page it could not read |
| G9 asset QA | A page with no approved version, two approved versions, below 300 DPI, wrong prompt, a tinted background, shading, colour, a clipped drawing, a corner mark; blocks for any page it could not read |
| G5 print preflight | No live area, unsafe spine text, no art pages, missing or unopenable files |
| G10 interior QA | Wrong page count, wrong page size, mixed page sizes, artwork missing from a page that should have it, low-resolution placement, art on a recto in a single-sided book, a PDF that changed after assembly |
| G11 cover QA | Wrong canvas width or height, a spine that does not match the current page count, unsafe spine text, a cover with no text, a file that changed after production |
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

## Inspection

Two layers, both real, and both reporting rather than repairing — the read-only
auditors above would mean nothing if the code beneath them could quietly fix
what it found.

**Pixels** (`kdp/inspection/image.py`). What a coloring page has to be is
unusually easy to state numerically — black strokes on white, nothing in
between, nothing coloured, nothing touching the edge — so most of these checks
are decidable rather than heuristic. A page with 12% of its pixels in the
midtones really does have shading in it.

| Measure | Fails when |
|---|---|
| Border luminance | Below 240 — the background is not paper |
| Midtone fraction | Above 6% — shading or greyscale fill |
| Ink fraction | Below 0.5% (nothing drawn) or above 60% (nothing left to colour) |
| Coloured fraction | Above 0.1% of pixels with channel spread > 24 |
| Outer-ring ink | Above 0.2% — the drawing is clipped by the trim |
| Corner ink | Above 4% — *warns*, does not block: the shape a watermark makes |
| Dimensions | Effective DPI over the live area below 300 |
| Enclosed area | Below 0.5% with strokes present — nothing can be coloured |
| Leak ratio | 1.05 or above — outlines have gaps and fills escape |

### Closed regions

A coloring page is a set of *enclosed* areas. If an outline has a gap, a fill
escapes across the sheet and the page is unusable, though it looks fine on
screen. `kdp/inspection/regions.py` labels the connected components of the
non-ink pixels: anything reachable from the page border is outside the drawing,
everything else is colourable area.

It runs on runs, not pixels. Line art is mostly long uninterrupted spans of
paper — under three per row for a full-page drawing — so labelling runs makes
exact analysis at print resolution cost about 180 ms. Nothing is downsampled
for speed, and no accuracy is traded away.

Gaps are found by comparing that measurement against a second pass in which
strokes are thickened enough to bridge breaks a few pixels wide. On a drawing
whose outlines are already closed the ratio lands just *below* 1.0, because
thickening also shaves area off every region — measured at 0.97–0.99 across the
fixture set. At or above 1.05, bridging genuinely recovered colourable area, so
there were gaps. Across 24 fixture pages that threshold caught 23 of 24
deliberately broken pages with no false positives on clean ones.

Deciding that one *particular* outline is broken would need the drawing
segmented into shapes. This reports totals and a leak ratio, which is a
printability signal for a human — never a copyright judgement.

**PDFs** (`kdp/inspection/pdf.py` measures, `checks.py` compares). Page count,
page dimensions against trim-plus-bleed, uniformity, which pages carry
artwork, embedded image resolution, single-sided layout, cover canvas, and the
spine implied by the canvas width against the spine the page count requires —
which is how a cover built before the interior grew gets caught.

### Capability is per artefact, not per install

"Can I inspect this?" is a better question than "is Pillow installed?". A PNG
is readable with the standard library alone; a JPEG is not. So the loaders
answer for the file in front of them, and a gate blocks only when *that
artefact* really could not be read. In practice a mock-generated book clears
pixel QA with nothing installed, and the gate still blocks the moment a page
it cannot decode appears.

PDFs are different: without `pypdf` there is no second implementation, and the
alternative — hand-rolling a reader for the format this package also writes —
would give an answer without giving an independent one. So G5, G10 and G11
block, and say what went unchecked.

### Two things it does not claim

**Text detection** needs OCR, which is not installed. The inspection reports
that it did not look, rather than that it found nothing; those are different
claims and only one is true.

**Similarity is not a copyright judgement.** The perceptual hash flags pages
within 10 bits of 64 of each other. That is a QA signal routed to a human, and
every message that carries it says so.

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

Overriding that takes a decision, not a flag: `--regenerate` names the pages to
redo. There is deliberately no redo-everything switch, because naming the pages
is what makes a spend deliberate. A metered provider additionally needs
`--confirm-spend`, on a first run and on a re-run alike.

## Economics

Both halves of the calculation are **tabular, not formulaic**, because KDP's
are.

**The royalty rate depends on the list price.** On amazon.com a paperback below
$9.99 earns 50%, not 60%. An earlier revision assumed 60% universally, which
overstated the return on every book under that line — including $5.99, the
price this pipeline was most likely to be pointed at. On a 60-page book that
error reported $1.87 a copy where the true figure is $0.70.

**The printing cost changes shape with page count.** Short books are charged a
flat fee with no per-page component; longer ones switch to fixed-plus-rate.
Applying the long-book formula to a 24-page book understates the cost by a
dollar, which is most of the margin.

**Royalty (amazon.com):** 50% at $9.98 or below, 60% at $9.99 or above.
Royalty is `(rate × list price) − printing cost`.

**Printing cost** is keyed by marketplace, ink, paper stock and trim class —
KDP prices on all four — and ten tables are recorded:

| Ink / stock | Regular trim | Large trim |
|---|---|---|
| Black, white or cream, 24–110pp | $2.30 flat | $2.84 flat |
| Black, white or cream, 110–828pp | $1.00 + $0.012/pp | $1.00 + $0.017/pp |
| Black, groundwood, 24–112pp | $2.23 flat | $2.75 flat |
| Black, groundwood, 114–828pp | $1.00 + $0.0114/pp | $1.00 + $0.0162/pp |
| Premium colour, 24–40pp | $3.60 flat | $4.20 flat |
| Premium colour, 42–828pp | $1.00 + $0.065/pp | $1.00 + $0.08/pp |
| Standard colour, 72–600pp | $1.00 + $0.0255/pp | $1.00 + $0.0402/pp |

**Trim class is derived, not transcribed.** KDP's rule is that large trim is
over 6.12" wide or over 9" high; `TrimSize.cost_class` applies it, and the
tests assert the derivation against KDP's published regular/large split. That
way a size added later is classified rather than defaulting to whatever someone
typed.

**8.5×11 is large trim** — the coloring-book default. Pricing it as regular
understates printing by 54¢ a copy. On a 60-page book at $5.99 the real margin
is **$0.155**, where treating it as regular reported $0.695: more than four
times over.

**Three anomalies in KDP's own table are transcribed faithfully and make the
model refuse rather than guess.** The black-ink bands are published as 24–110
*and* 110–828, so exactly 110 pages carries two different prices; groundwood
jumps 112→114 and premium colour jumps 40→42, leaving 113 and 41 pages with no
published price at all. Each raises and names what it found. The `royalty`
table stays UNVERIFIED because of them: a source that contradicts itself has
been read, not resolved.

**Projections report at `ESTIMATED` confidence.** Rate, cost bands and trim
class are all verified now; what keeps the roll-up soft is a single declared
unknown — the model computes `rate × price − printing cost` and does not model
VAT, delivery or marketplace deductions, so a real payout can be lower. That is
recorded as an assumption rather than left implied.

## Image generation providers

Three, behind one interface, chosen with `--provider`:

| Provider | Metered | State |
|---|---|---|
| `mock` | no | Deterministic, offline. What CI runs. |
| `google-imagen` | **yes** | Implemented against the official `google-genai` SDK. Needs a key. |
| `higgsfield` | **yes** | Boundary only — no contract was available. |

### google-imagen

Written against the SDK's own typed surface —
`Client.models.generate_images(model, prompt, config)` taking a
`GenerateImagesConfig` and returning `generated_images[].image.image_bytes` —
read from the installed package rather than recalled. That is the whole
difference from Higgsfield: there, no contract could be obtained, so no client
was written.

Three properties worth knowing:

**Credentials are never picked up ambiently.** The key is read by name from
`KDP_GOOGLE_API_KEY`, `GOOGLE_API_KEY` or `GEMINI_API_KEY`, and `vertexai=False`
is passed explicitly so that application-default credentials, a gcloud token or
a stray `GOOGLE_GENAI_USE_VERTEXAI` cannot route generation onto a cloud project
nobody chose. A provider that spends whatever credentials happen to be in the
environment is worse than one that does not work.

**Prohibitions go in the negative prompt.** `AssetPrompt.prohibited` is already
exactly that list, and it is passed as `negative_prompt` rather than appended to
the positive prompt, where a model tends to draw the thing it was told to avoid.
`render()` still returns the combined form for providers with no negative
prompt.

**The returned size is measured, not assumed.** Imagen picks the output
dimensions, so the provider decodes the bytes and records what actually came
back. Asset QA compares the provenance record against the file, and a guessed
size would fail it.

Everything is tested offline against a stub shaped like the real response
types, so CI never needs a key and never pays for a picture.

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

**Verify the specification tables — the one blocker that cannot be cleared
from inside this package.** Every `amazon.com` host is refused by network
policy in the development environment (403 on CONNECT), so no value has been
confirmed against a primary source. G2, G12 and G13 refuse to certify a book
while that stands, and `python3 -m kdp verify-specs` prints exactly what to
read and what to confirm on each page.

Where a URL is recorded it was seen in a search result and is a starting point;
where only a page title is given, no URL is recorded, because a confidently
wrong link sends someone to the wrong page and they verify against it.

Five of the six tables were verified against official KDP pages on 2026-08-31
for Amazon.com/US, with URLs, dates and a spec version recorded. `royalty` is
sourced but unresolved — see the three source anomalies above — so the final
audit still blocks, while the geometry and policy gates no longer need an
override.

Verification corrected four things, all of which had been silently wrong: the
spine-text threshold (80 pages, not 100), the trim table (8.25×8.25 was
missing), the colour spine caliper (one figure for both tiers), and — the
expensive one — that 8.5×11 is large trim.

**The Higgsfield HTTP client.** See above — a boundary, pending an API
contract.

**Research fetch.** Wire `ornith-cloud`'s retrieval pipeline behind the
researcher agent, keeping the G1 whitelist as the boundary.

**Generation cost control.** Port the operation-registry pattern from
`embroidery-genie-ai`'s AI gateway, with a file-backed ledger, once a metered
provider is actually in use.
