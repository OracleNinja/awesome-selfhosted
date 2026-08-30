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
python3 -m pytest                 # 176 tests; needs only pytest
python3 -m kdp stages             # the pipeline
python3 -m kdp gates              # every gate, its purpose, and what it needs
python3 -m kdp specs --trim 8.5x11 --pages 60
```

The core — models, print geometry, gates, assembly, the PDF writer, the CLI —
is standard library only. The extras in `requirements.txt` unlock pixel
inspection (G4, G9) and PDF inspection (G5, G10, G11); until they are installed
those gates report `BLOCKED`, which **stops** a book rather than passing it.

## Building a book

```bash
python3 -m kdp research        books/<id>/research.json   # G1
python3 -m kdp strategy        books/<id>/manifest.json   # G7
python3 -m kdp concept         books/<id>/manifest.json   # G2
python3 -m kdp planning        books/<id>/manifest.json   # G8
python3 -m kdp generate        books/<id>/manifest.json --provider mock
python3 -m kdp asset           books/<id>/manifest.json approve <asset-id>
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

`kdp/specs/revision.py` carries a source record per specification table, and
all of them are `UNVERIFIED` — the numbers came from secondary sources because
Amazon's own pages were unreachable from the environment this was built in.
G2, G12 and G13 refuse to certify a book while that stands.

Read the primary KDP Help pages, confirm each table, record the URL and the
date you checked. Verifications expire after 180 days.

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
