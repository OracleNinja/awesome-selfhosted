# KDP Studio

A gated pipeline for producing **original** low-content books — coloring books
and similar — for Amazon KDP.

Six stages, each independently runnable, each separated from the next by a
deterministic quality gate. A book that fails originality, duplication,
formatting, provenance or KDP-compliance checks does not advance.

**It does not publish anything.** The pipeline ends at
`ready_for_submission`; a human fills in the KDP form.

## Quick start

```bash
cd kdp-studio
python3 -m pytest                       # 108 tests, no dependencies needed
python3 -m kdp specs --trim 8.5x11 --pages 60
python3 -m kdp stages
```

The core is stdlib-only on purpose, so a gate can always be evaluated. The
extras in `requirements.txt` unlock near-duplicate detection (G4) and deep PDF
preflight (G5); until they are installed those gates report `BLOCKED`, which
stops a book rather than passing it.

## Before the first real book

`kdp/specs/revision.py` is marked `UNVERIFIED` — the print tables were
transcribed from secondary sources because Amazon's own pages were unreachable
from the environment this was built in. Gate G2 fails every book while that
flag stands. Verify the numbers against KDP Help, then flip it.

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the audit this was designed from,
  agents, gates, data flow, and the implementation plan.
- [`COMPLIANCE.md`](COMPLIANCE.md) — KDP policy constraints and where each one
  is enforced.

## Agents

Seven agents in `.claude/agents/`, coordinated by `kdp-orchestrator`, which is
the only one permitted to advance a book. The QA auditor has read-only tools:
it reports findings and never fixes them.

Slash commands: `/kdp:status`, `/kdp:gate`, `/kdp:new-book`.
