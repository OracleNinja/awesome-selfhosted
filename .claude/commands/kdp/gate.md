---
description: Run a KDP quality gate and explain the result
argument-hint: [stage] [path to manifest.json]
allowed-tools: Read, Bash, Glob
---

Run the `$1` gate against `$2` using `python3 -m kdp --json $1 $2`.

Stages: `research`, `concept`, `generation`, `qa`, `production`,
`publishing-prep`.

Then explain the result:

- **pass** — say so, and name the next stage.
- **fail** — list each blocker finding with its code, subject and message, and
  say which stage owns the fix.
- **blocked** — explain that the gate could not be evaluated, why (a missing
  dependency, or missing evidence such as the PDFs), and what would let it run.
  Be explicit that this is not a pass and the book has not cleared the gate.

Never suggest narrowing a check (`--exact-match-only`,
`--allow-unverified-specs`) as a way past a blocker unless the user asks; if
you do mention one, say exactly what it stops checking.
