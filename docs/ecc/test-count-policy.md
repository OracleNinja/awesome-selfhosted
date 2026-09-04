# Test-count policy

Phase 2L. Since Phase 2K the verification harness reports how many tests each
step executed. This records what we do with those numbers, decided per suite
rather than as one blanket rule.

Three outcomes are available:

- **ENFORCE** — CI fails if the count drops below a committed baseline.
- **REPORT ONLY** — the count is printed and published; a drop is visible to a
  reviewer but does not fail the build.
- **NOT USEFUL** — a count is meaningless for this step; it declares
  `count: { strategy: 'none' }` and reads as `n/a`.

## Decisions

| suite | baseline | outcome |
|---|---|---|
| ornith-cloud `test` | 87 | REPORT ONLY |
| ornith-desktop `unit` | 138 | REPORT ONLY |
| ornith-desktop `integration` | 34 | REPORT ONLY |
| ornith-desktop `e2e-electron` | 19 | REPORT ONLY |
| backend required (`--ignore=tests/test_api.py`) | 124 collected / 79 without a database | REPORT ONLY |
| backend conditional (`tests/test_api.py`) | 42 | REPORT ONLY |
| install / build / typecheck / lint steps | — | NOT USEFUL |
| `tools/verify/run.test.js` (harness self-test) | 88 | REPORT ONLY |

No suite is ENFORCE today. That is a deliberate decision, not an omission.

## Reasoning

**Stability is good, and stability is not the deciding factor.** ornith-cloud
reported 87 on three consecutive local runs and again in CI; ornith-desktop
reported 138 and 34 identically in both environments. Counts are deterministic
here — none of these suites is randomised or time-dependent. So an ENFORCE gate
would not be flaky. The question is whether it would be *useful*, and that turns
on whether a decrease is always wrong.

**It is not always wrong.** These products are early. Test consolidation during
refactoring legitimately reduces a count: three overlapping cases become one
table-driven case, a suite is restructured, a feature is removed with its tests.
An ENFORCE gate treats every such change as a failure and trains people to
update the baseline as a reflex — at which point the gate stops being read and
starts being a chore. A gate that is routinely bypassed is worse than no gate,
because it looks like protection.

**The risk we actually care about is silence, and that is now solved.** The
failure this came from is a suite shrinking without anyone noticing. That was
possible when a green run said nothing about how much it ran. It is no longer
possible: every run prints per-step counts, CI publishes them to the job
summary, and Phase 2K proved with fixtures that an 87-test and a 3-test run are
distinguishable in both the report and the printed output while both are green.
Visibility, not enforcement, is what was missing.

**Enforcement would add new untested machinery.** A gate needs a committed
baseline file, a comparison step, and a policy for updating it — new code whose
own failure modes (a stale baseline failing CI on an unrelated PR) are more
likely in practice than the regression it guards against.

### Per suite

**ornith-cloud `test` (87)** — deterministic, stable across three local runs and
CI. A drop would mean deleted tests or broken collection. But the product is
young and consolidation is expected, so a drop is not reliably a defect.
False-positive risk under ENFORCE: moderate and recurring. → REPORT ONLY.

**ornith-desktop `unit` (138) and `integration` (34)** — same reasoning; both
matched exactly between local and CI. → REPORT ONLY.

**ornith-desktop `e2e-electron` (19)** — observed exactly once, in the optional
CI job under Xvfb. One observation is not a baseline. Enforcing a number seen
once would be enforcing a guess. Revisit if it stabilises across runs.
→ REPORT ONLY.

**backend (124 required / 42 conditional)** — this suite has two baselines
depending on whether a database is present (79 executed without, 124 with), and
it was restructured in this very phase to stop double-running. Pinning a count
to a suite whose shape just changed is the textbook false-positive case. The
split is also fragile in a way a count cannot see: it is path-based, valid only
while `tests/test_api.py` stays 100% DB-gated, and an always-run test added
there would be silently excluded from the no-database step. A count gate would
not catch that; only the fragility note in the spec and a reviewer will.
→ REPORT ONLY.

**install, build, typecheck, lint** — `npm ci`, `tsc --noEmit`, `eslint .` and
`pip install` run no tests and write no result artefact. A count is not a number
these can produce. They declare `strategy: 'none'` and read as `n/a`, which is
distinct from UNKNOWN and asserts the fact rather than hiding it.
→ NOT USEFUL.

**`tools/verify/run.test.js` (88)** — the strongest ENFORCE candidate. It is
infrastructure, fully deterministic with no external dependencies, and each
assertion encodes a guarantee about how PASS/FAIL/SKIP/WARN and counts behave;
a silent decrease means a guarantee was deleted. It stays REPORT ONLY for now
only because the standing rule already forbids weakening or deleting tests, and
that rule plus review covers the same ground without new machinery. This is the
first suite to reconsider if the harness ever gains contributors who are not
operating under that rule.

## When to revisit

- A count drops and nobody notices until much later — visibility alone was not
  enough, and ENFORCE earns its cost.
- A suite stops churning and its count holds across many runs and contributors.
- The harness gains outside contributors, making `run.test.js` a real target.
