# KDP Studio

An agent-driven pipeline for producing **original** low-content books —
coloring books first, extensible to other KDP formats — from market research to
a publication-ready package.

Eleven stages, fourteen deterministic quality gates, and one gate no automation
can satisfy. A book that fails originality, duplication, formatting,
provenance or KDP-compliance checks does not advance.

**It does not publish anything.** The pipeline ends at
`ready_for_submission`; a human reads the review package, records a decision,
and fills in the KDP form.

## Quick start

```bash
cd kdp-studio
python3 -m pytest                 # 340 tests; needs only pytest
python3 -m kdp stages             # the pipeline
python3 -m kdp gates              # every gate, its purpose, and what it needs
python3 -m kdp specs --trim 8.5x11 --pages 60
```

The core — models, print geometry, gates, assembly, the PDF writer, the pixel
inspector, the CLI — is standard library only.

Inspection capability is decided **per artefact**, not per install. A PNG is
readable with the standard library alone, so pixel QA genuinely runs on a
mock-generated book with nothing installed; a JPEG needs Pillow, and a page
that cannot be read blocks the gate. PDF inspection needs `pypdf` outright,
because without it there is no implementation independent of the writer — so
G5, G10 and G11 block and say what went unchecked.

## Building a book

```bash
python3 -m kdp research        books/<id>/research.json   # G1
python3 -m kdp strategy        books/<id>/manifest.json   # G7
python3 -m kdp concept         books/<id>/manifest.json   # G2
python3 -m kdp planning        books/<id>/manifest.json   # G8
python3 -m kdp generate        books/<id>/manifest.json --provider mock
python3 -m kdp asset           books/<id>/manifest.json approve <asset-id>
#   ...or, to redo named pages:  --regenerate PAGE-004 PAGE-012
python3 -m kdp generation      books/<id>/manifest.json   # G3
python3 -m kdp qa              books/<id>/manifest.json   # G4, G9
python3 -m kdp build           books/<id>/manifest.json
python3 -m kdp production      books/<id>/manifest.json --interior … --cover …
python3 -m kdp production-qa   books/<id>/manifest.json   # G10, G11
python3 -m kdp publishing-prep books/<id>/manifest.json   # G6, G12
python3 -m kdp final-audit     books/<id>/manifest.json   # G13
python3 -m kdp review          books/<id>/manifest.json --out review.md

#  ── a human reads review.md and decides ──
python3 -m kdp approve         books/<id>/manifest.json approve --reviewer <name>

python3 -m kdp package         books/<id>/manifest.json   # G14 must pass
```

Exit codes: `0` passed, `1` stopped, `2` misused.

Resuming a failed run costs only what is unfinished: `generate` skips pages
that are approved or awaiting a QA verdict.

## What inspection actually checks

**Pixels** — background purity, ink coverage, shading and greyscale, colour,
clipping at the trim edge, effective print resolution, closed regions (whether
a fill would stay inside the outlines), and a corner-mark heuristic for
watermarks and signatures (a *warning*, never a verdict). Near-duplicates are
found with a difference hash.

**PDFs** — page count, page dimensions against trim-plus-bleed, uniform page
size, artwork on exactly the pages the spec describes, embedded image
resolution, single-sided layout, cover canvas, and the spine implied by the
canvas against the spine the page count requires.

Two things it does not claim: **text detection** needs OCR, which is not
installed, so the report says it did not look rather than that it found
nothing; and **similarity is not a copyright judgement** — the hash flags pages
for a human, and says so.

## Three properties worth knowing

**A gate that cannot run does not pass.** `PASS`, `FAIL`, `BLOCKED` — and only
`PASS` advances. Treating "I could not check" as "nothing found" would turn an
uninstalled library into a silent quality regression.

**Unknown provenance fails closed.** An unclassified asset never rolls up to
"no AI disclosure required". The manifest's disclosure block is re-derived on
read, so hand-editing it changes nothing a gate sees.

**Approval is bound to a fingerprint.** Editing the assets, the PDFs, the
listing or the price after sign-off invalidates the approval, and packaging
refuses.

## Before the first real book

```bash
python3 -m kdp verify-specs
```

All six specification tables are `UNVERIFIED`. Every `amazon.com` host is
refused by network policy in the development environment, so no value has been
confirmed against a primary source, and **G2, G12 and G13 refuse to certify a
book** while that stands.

`verify-specs` prints the page to read and what to confirm on it, table by
table. Record `source_url`, `verified_on` and `spec_version` in
`kdp/specs/revision.py`; that alone flips a table. Verifications expire after
180 days. Secondary sources — blogs, calculators, summaries — do not count.

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the audit this was designed from,
  agents and why only three were added, stages, gates, provenance, economics,
  the Higgsfield boundary, and what remains.
- [`COMPLIANCE.md`](COMPLIANCE.md) — KDP policy constraints and where each is
  enforced.

## Agents

Ten in `.claude/agents/`, coordinated by `kdp-orchestrator`, which reads exit
codes and never judges quality itself. Two are read-only — `kdp-qa-auditor` and
`kdp-compliance-auditor` — because an auditor that repairs its own findings has
an interest in finding fewer of them.

Slash commands: `/kdp:status`, `/kdp:gate`, `/kdp:new-book`.
