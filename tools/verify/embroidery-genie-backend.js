'use strict';

// Evidence-derived, declarative verification spec for embroidery-genie-ai's
// backend. This file only describes commands; nothing here is executed by
// this file itself. Semantics come from embroidery-genie-ai/README.md,
// backend/pyproject.toml, backend/tests/conftest.py, and — for the two
// interpreter/installer defects below — a real execution run by the Lead.

module.exports = {
  id: 'embroidery-genie-backend',
  title: 'Embroidery Genie AI backend (FastAPI, Python 3.11)',
  dir: 'embroidery-genie-ai/backend',

  // README.md:111-113 names plain `pip install -r requirements.txt`, but on
  // a clean container it failed: pip could not uninstall the Debian-packaged
  // PyJWT ("Cannot uninstall PyJWT 2.7.0, RECORD file not found... installed
  // by debian", /usr/lib/python3/dist-packages) before installing the pinned
  // pyjwt[crypto]==2.13.0 (requirements.txt:18). --ignore-installed installs
  // the pinned versions alongside the dpkg-owned ones instead of removing
  // them — confirmed: exit 0 in ~23s, then key imports succeeded. numpy /
  // opencv-python-headless / pillow / shapely wheels justify a long timeout.
  install: {
    command: 'python3',
    args: ['-m', 'pip', 'install', '--ignore-installed', '-r', 'requirements.txt'],
    timeoutMs: 600000,
    // A pip install writes no junit/vitest/playwright artifact, so none of
    // the three real count strategies has anything to parse, and inventing
    // a number would violate run.js's own rule that a wrong count is worse
    // than no count (run.js:118). Previously no `count` was declared here;
    // `pip install ...` does not match looksLikePytest (run.js:184-189
    // only recognizes a bare `pytest` or `python -m pytest`-shaped
    // command), so run.js:204's "no count strategy declared, and none can
    // be inferred" branch applied, rendered by run.js:236 as `tests
    // UNKNOWN`. That is indistinguishable from a step that silently lost
    // its ability to be counted. Declaring `strategy: 'none'` instead
    // (run.js:194-195,208) renders as `tests n/a` (run.js:235) — an
    // explicit statement that a count does not apply to a dependency
    // install, not an unexplained gap. Same convention as
    // ornith-desktop.js:25.
    count: {
      strategy: 'none',
      reason: 'dependency install (pip), not a test runner — no artifact to count',
    },
  },

  // Required: same interpreter fix as before — bare `pytest` resolves to a
  // uv-managed tool with its own isolated Python (/root/.local/bin/pytest)
  // and fails with "ModuleNotFoundError: No module named 'PIL'"/"'shapely'",
  // so this stays `python3 -m pytest`, the interpreter `python3 -m pip`
  // installed into.
  //
  // CHANGED in this revision to fix a double-execution defect: this step now
  // adds `--ignore=tests/test_api.py`. test_api.py:17 sets
  // `pytestmark = requires_db` at module scope (conftest.py:31 defines
  // `requires_db` via `pytest.mark.skipif`), so every test in that file is
  // DB-gated with no per-test exception — independently measured by the
  // Lead (42 collected, 42 DB-gated, 0 always-run) and confirmed by me via
  // grep of test_api.py: exactly one `pytestmark = requires_db` assignment,
  // applied to the whole module. That makes test_api.py the only file in
  // the suite excludable by path without splitting a mixed file.
  //
  // Before this change, this step and the conditional step below ran the
  // identical unfiltered `python3 -m pytest` command; with TEST_DATABASE_URL
  // set, both steps executed the complete 166-test suite, so the harness
  // counted 332 executions of the same 166 tests. Excluding test_api.py here
  // and restricting the conditional step to exactly that file (see below)
  // removes the overlap.
  //
  // test_ai_cost_controls.py and test_hardening.py are UNCHANGED and stay in
  // this step: they mix DB-gated and always-run tests in the same file
  // (38/60 and 7/25 DB-gated per the Lead's measurement; I independently
  // grepped `@requires_db` decorator sites and count 38 and 7, matching
  // exactly), conftest.py:31's skipif gates per-test rather than per-file,
  // and `skipif` is not a registered marker so `-m` cannot select it either.
  // Adding a registered marker to conftest.py or the product tests to make a
  // cleaner split was out of scope for this change, so these two files still
  // rely on their own per-test skipif, exactly as before this revision.
  //
  // Counts (arithmetic from the Lead's table — this exact `--ignore`
  // invocation has not been executed by me or, to my knowledge, by anyone
  // yet; see notes[]):
  //   no TEST_DATABASE_URL: 124 collected (166 minus test_api.py's 42),
  //     79 passed, 45 skipped (38 + 7, the DB-gated tests in the two mixed
  //     files). test_api.py's 42 tests are absent from this step's own
  //     report entirely — not collected, not counted here — and are instead
  //     accounted for by the conditional step's own step-level SKIP.
  //   TEST_DATABASE_URL set: 124 collected, 124 passed, 0 skipped — the two
  //     mixed files' skipif conditions no longer trigger.
  // pytest config (testpaths, addopts) is pyproject.toml:7-10.
  verify: [
    {
      name:
        'python3 -m pytest --ignore=tests/test_api.py (engine + mixed ' +
        'suites; DB-gated tests inside them skip honestly without ' +
        'TEST_DATABASE_URL; test_api.py is 100% DB-gated and runs only in ' +
        'the conditional step below, excluded here so it is never executed ' +
        'in both steps at once)',
      command: 'python3',
      args: ['-m', 'pytest', '--ignore=tests/test_api.py'],
      timeoutMs: 300000,
    },
  ],

  // Conditional: CHANGED in this revision from the full suite to
  // `tests/test_api.py` only. Same interpreter fix as above (bare `pytest`
  // is the uv-isolated tool, not the pip-installed environment); same
  // env-detection convention — requiresEnv matches conftest.py:26 exactly,
  // and conftest.py:26-28 reads TEST_DATABASE_URL and mirrors it into
  // DATABASE_URL — README.md:160-162.
  //
  // Why this scope is now safe to keep as its own step: test_api.py is the
  // one file in the suite that is 100% DB-gated (test_api.py:17, module-wide
  // `pytestmark = requires_db`; Lead's table: 42/42). The required step
  // above now excludes exactly this file (--ignore=tests/test_api.py), so
  // the two steps no longer overlap on a single test: required covers the
  // other 124 (79 always-run + 45 DB-gated-but-mixed-file, still gated
  // per-test by conftest.py:31), this step covers the remaining 42, and
  // 124 + 42 = 166 — the full collected suite, each test in exactly one
  // step. Previously this step ran the identical unfiltered
  // `python3 -m pytest` that the required step also ran, so a database
  // being present meant every one of the 166 tests executed twice; that is
  // the defect this revision removes.
  //
  // Representation choice unchanged from before: a separate command, not a
  // notes-only caveat on the required entry — a notes-only version would
  // never actually execute these tests even with a real database available.
  // Without the var the runner must not invoke this entry (SKIP, never
  // PASS); with it, pytest fails for real failures exactly as the required
  // entry does.
  //
  // FRAGILITY (file-path split): correct only as long as test_api.py stays
  // 100% DB-gated. If an always-run (non-DB) test is later added to
  // test_api.py, the required step's --ignore=tests/test_api.py excludes
  // the whole file unconditionally, so that new test would never execute —
  // not even as a reported SKIP — in the no-database configuration; it
  // would only run here, and only when TEST_DATABASE_URL happens to be set.
  // That is a silent required-tier coverage gap, not a double-execution,
  // and nothing in this spec would catch it — only re-running the
  // collect-only/skip-count measurement would. The reverse risk does not
  // apply: this split does not depend on test_ai_cost_controls.py or
  // test_hardening.py staying any particular shape, because those two stay
  // fully inside the required step and are still gated per-test by
  // conftest.py:31, unchanged by this revision.
  //
  // Counts (arithmetic from the Lead's table; this exact restricted command
  // has not been executed in this form by me or, to my knowledge, by
  // anyone yet — see notes[]): no TEST_DATABASE_URL — SKIP, 0 executed.
  // TEST_DATABASE_URL set — 42 collected, 42 passed, 0 skipped.
  conditional: [
    {
      name:
        'python3 -m pytest tests/test_api.py (API/DB integration tests, ' +
        '100% DB-gated per test_api.py:17; requires a real PostgreSQL ' +
        'database)',
      command: 'python3',
      args: ['-m', 'pytest', 'tests/test_api.py'],
      requiresEnv: 'TEST_DATABASE_URL',
    },
  ],

  // Optional: dev/system tooling outside the pinned dependency set; must
  // never be reported as a pass/fail gate.
  optional: [
    {
      name: 'ruff lint',
      command: 'ruff',
      args: ['check', '.'],
      reason:
        'ruff is configured (pyproject.toml:13-26) but is not pinned in ' +
        'requirements.txt:1-41, so it may be absent from the installed ' +
        'environment; informational only.',
    },
    {
      name: 'potrace availability',
      command: 'potrace',
      args: ['--version'],
      reason:
        'potrace is an optional system package (README.md:139-148) that ' +
        'improves curve tracing; the OpenCV contour path is the documented ' +
        'fallback (README.md:144-146), so its absence is not a failure.',
    },
  ],

  notes: [
    'install and required-verify commands above were corrected against a ' +
      'real execution by the Lead (not authored from reading alone): ' +
      'plain pip failed on dpkg-owned PyJWT, plain pytest resolved to a ' +
      'uv-isolated interpreter missing PIL/shapely — both fixed by the ' +
      'python3 -m ... forms now used, and both re-confirmed working.',
    'requirements.txt:41 pins pytest==9.0.2; no other test runner is ' +
      'referenced anywhere under backend/.',
    'tests/test_engine.py has no reference to requires_db or app_client ' +
      '(checked), so it is unconditionally covered by the required tier.',
    'tests/test_api.py:15,17; tests/test_ai_cost_controls.py:18,26-27; ' +
      'and tests/test_hardening.py:14 each use requires_db or check ' +
      'TEST_DATABASE_URL directly, confirming DB-gating is enforced at ' +
      'the test level, not the file level, per conftest.py:1-9.',
    'The conditional (DB-present) run has since been executed against a ' +
      'live PostgreSQL database — per the Lead\'s report, not run by me. ' +
      'Locally: PostgreSQL 16.13 with TEST_DATABASE_URL set to a psycopg ' +
      'URL, "python3 -m pytest" reported 166 passed, 0 skipped, 0 failed ' +
      '(vs. 79 passed, 87 skipped with the variable unset). In CI: GitHub ' +
      'Actions run 32755670933, job "embroidery-genie-backend (with ' +
      'database)" (required, not advisory), supplied a postgres:16 service ' +
      'container gated on pg_isready and set TEST_DATABASE_URL; the ' +
      'conditional step reported PASS, "(conditional, TEST_DATABASE_URL ' +
      'set)", 26.7s, exit 0. All 87 no-DB skips trace to the single cause ' +
      'TEST_DATABASE_URL being unset, across 82 skip sites — ' +
      'test_ai_cost_controls.py (38), test_api.py (37), test_hardening.py ' +
      '(7) — no skip in this suite has any other cause. Not established: ' +
      'behaviour against PostgreSQL major versions other than 16 ' +
      '(16.13 ran locally, 16.15 ran in CI in run 32755670933, both ' +
      'passing), ' +
      'flakiness (each path run only a handful of times), or any Alembic ' +
      'migration path — the schema here comes from ' +
      'Base.metadata.create_all() in the app_client fixture, not a ' +
      'migration. NOTE: the "166 passed, 0 skipped" run described in this ' +
      'paragraph, and the referenced CI run 32755670933, both exercised ' +
      'the OLD unfiltered `python3 -m pytest` conditional command (the one ' +
      'this revision replaces), not the new tests/test_api.py-only form — ' +
      'see the DEFECT FIX note below for what changed and what is still ' +
      'unverified.',
    'DEFECT FIX (this revision, applied by the ecc-worker in response to ' +
      'the Lead\'s report): before this change, the required step ran ' +
      '`python3 -m pytest` (full suite) and the conditional step ran the ' +
      'identical command gated on TEST_DATABASE_URL. Neither filtered by ' +
      'path, so when TEST_DATABASE_URL was set both steps executed the ' +
      'complete 166-test suite — the harness reported "tests: 332 ran", ' +
      '332 genuine executions of the same 166 tests, twice each. Fixed by ' +
      'excluding tests/test_api.py from the required step ' +
      '(args now include --ignore=tests/test_api.py) and restricting the ' +
      'conditional step to exactly that file (args now ' +
      '[\'-m\', \'pytest\', \'tests/test_api.py\']). test_api.py:17 sets ' +
      '`pytestmark = requires_db` at module scope — the whole file is ' +
      'DB-gated, confirmed by the Lead\'s collect-only/skip-count table ' +
      '(42 collected, 42 DB-gated, 0 always-run) and by my own grep of the ' +
      'file (a single module-wide pytestmark assignment, no per-test ' +
      'override). That is the only file in the suite where a path-based ' +
      'exclusion does not risk silently dropping an always-run test that ' +
      'happens to share a file with DB-gated ones. ' +
      'test_ai_cost_controls.py and test_hardening.py were left untouched ' +
      'in the required step because they mix DB-gated and always-run ' +
      'tests in the same file (38/60 and 7/25 DB-gated per the Lead\'s ' +
      'table; I independently grepped `@requires_db` decorator sites in ' +
      'both files and count 38 and 7 respectively, matching exactly) and ' +
      'conftest.py:31\'s skipif marker is not registered, so `-m` cannot ' +
      'select it — a per-file split of those two would require adding a ' +
      'registered marker to conftest.py, which was explicitly out of scope.',
    'Expected per-step counts after this fix (arithmetic derived from the ' +
      'Lead\'s measured table, NOT independently re-executed — this ' +
      'ecc-worker has no Bash and could not run pytest; the Lead should ' +
      'confirm these against a real execution before treating them as ' +
      'fact): without TEST_DATABASE_URL — required step 124 collected / 79 ' +
      'passed / 45 skipped, conditional step SKIP (0 executed), total 79 ' +
      'tests ran. With TEST_DATABASE_URL set — required step 124 collected ' +
      '/ 124 passed / 0 skipped, conditional step 42 collected / 42 passed ' +
      '/ 0 skipped, total 166 tests ran — each of the 166 tests executed ' +
      'exactly once across the two steps, versus 332 before this fix.',
    'FRAGILITY of the file-path split (named per the Lead\'s explicit ' +
      'request): it is correct only for as long as test_api.py remains ' +
      '100% DB-gated. An always-run test added to test_api.py in the ' +
      'future would be silently excluded from the required (no-database) ' +
      'step by --ignore=tests/test_api.py, and would only ever execute in ' +
      'the conditional step when TEST_DATABASE_URL happens to be set — a ' +
      'required-tier coverage gap this spec has no mechanism to detect on ' +
      'its own. Re-running the Lead\'s collect-only/skip-count method ' +
      'against test_api.py periodically is the only way to catch that ' +
      'drift; nothing here automates it.',
    'install step count classification: previously undeclared, which fell ' +
      'through run.js\'s inference (looksLikePytest, run.js:184-189) to ' +
      '"no count strategy declared, and none can be inferred" ' +
      '(run.js:204), rendered as `tests UNKNOWN` (run.js:236) — ' +
      'indistinguishable from a step whose count silently failed. A pip ' +
      'install writes no junit/vitest/playwright artifact, so no real ' +
      'strategy has anything to parse and a count would have to be ' +
      'invented; declared `count: { strategy: \'none\', reason: ... }` ' +
      'instead (run.js:194-195,208), which renders as the explicit `tests ' +
      'n/a` (run.js:235). Same convention already used at ' +
      'ornith-desktop.js:25 for its own dependency-install step.',
  ],
};
