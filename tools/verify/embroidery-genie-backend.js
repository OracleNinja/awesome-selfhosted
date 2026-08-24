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
  },

  // Required: full suite, no database, via the interpreter pip installed
  // into. Bare `pytest` resolved to a uv-managed tool with its own isolated
  // Python (/root/.local/bin/pytest, shebang pointing at a uv tool venv)
  // and failed with "ModuleNotFoundError: No module named 'PIL'"/"'shapely'".
  // `python3 -m pytest` uses the same interpreter `python3 -m pip` installed
  // into and was confirmed: "79 passed, 87 skipped in 11.62s" with
  // TEST_DATABASE_URL unset. The 87 skips are exactly the DB-gated tests —
  // conftest.py:5 ("engine tests need only the Python dependencies and
  // always run"); test_hardening.py:14 mixes DB-free and requires_db-gated
  // tests in one file, so file-level selection can't separate them, and this
  // instead relies on conftest.py:26-41's own skip (never pass) behaviour,
  // applied module-wide by test_api.py:15,17 — matching README.md:172-173.
  // pytest config (testpaths, addopts) is pyproject.toml:7-11.
  verify: [
    {
      name: 'python3 -m pytest (engine tests required; DB-gated tests skip honestly without TEST_DATABASE_URL)',
      command: 'python3',
      args: ['-m', 'pytest'],
      timeoutMs: 300000,
    },
  ],

  // Conditional: identical command, same interpreter fix as above (bare
  // `pytest` is the uv-isolated tool, not the pip-installed environment),
  // with TEST_DATABASE_URL set so the tests that skip above now execute for
  // real — README.md:160-162; conftest.py:26-28 (reads TEST_DATABASE_URL,
  // mirrors into DATABASE_URL). requiresEnv matches conftest.py:26 exactly.
  // Representation choice: a separate command, not a notes-only caveat on
  // the required entry — a notes-only version would never actually execute
  // the DB tests even when a real database is available, leaving this tier
  // of the interface unused. Without the var the runner must not invoke
  // this entry (SKIP, never PASS); with it, pytest fails for real failures
  // exactly as the required entry does.
  conditional: [
    {
      name: 'python3 -m pytest (API/DB integration tests, requires a real PostgreSQL database)',
      command: 'python3',
      args: ['-m', 'pytest'],
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
      'migration.',
  ],
};
