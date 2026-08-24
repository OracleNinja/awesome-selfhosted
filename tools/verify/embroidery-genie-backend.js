'use strict';

// Evidence-derived, declarative verification spec for embroidery-genie-ai's
// backend. This file only describes commands; nothing here is executed by
// this file itself. Install/test semantics come from
// embroidery-genie-ai/README.md, embroidery-genie-ai/backend/pyproject.toml
// and embroidery-genie-ai/backend/tests/conftest.py — see notes below for
// exact citations.

module.exports = {
  id: 'embroidery-genie-backend',
  title: 'Embroidery Genie AI backend (FastAPI, Python 3.11)',
  dir: 'embroidery-genie-ai/backend',

  // README.md:111-113 ("cd backend" / "pip install -r requirements.txt").
  // requirements.txt:1-41 is the pinned dependency set the suite is
  // verified against (requirements.txt:2), including large binary wheels
  // (numpy, opencv-python-headless, pillow, shapely) — timeout is a
  // generous, unmeasured estimate for that reason.
  install: {
    command: 'pip',
    args: ['install', '-r', 'requirements.txt'],
    timeoutMs: 600000,
  },

  // Required: the full suite, run with no database configured. Engine
  // tests "need only the Python dependencies and always run"
  // (conftest.py:5). The remaining files cannot be excluded by filename —
  // test_hardening.py mixes DB-free tests (e.g. decompression-bomb,
  // stitch-budget checks) with requires_db-gated ones in the same file
  // (test_hardening.py:14), so file-level selection would either drop real
  // engine coverage or include tests that need filtering some other way.
  // Instead this relies on the suite's own documented skip behaviour:
  // conftest.py:26-41 makes app_client (and anything depending on it) call
  // pytest.skip() when TEST_DATABASE_URL is unset, and test_api.py:15,17
  // applies that as a module-wide pytestmark. pytest reports those as
  // SKIPPED, never PASSED, so this command cannot misreport DB coverage it
  // did not perform — see README.md:172-173 ("API tests skip cleanly when
  // TEST_DATABASE_URL is unset, so pytest stays useful on a laptop with no
  // database running."). pytest config (testpaths=["tests"], addopts="-q")
  // is pyproject.toml:7-11, so no args are required.
  verify: [
    {
      name: 'pytest (engine tests required; DB-gated tests skip honestly without TEST_DATABASE_URL)',
      command: 'pytest',
      args: [],
      timeoutMs: 300000,
    },
  ],

  // Conditional: re-run the identical suite with TEST_DATABASE_URL set, so
  // the tests that skip above now actually execute against a real
  // Postgres database — README.md:160-162 ("createdb ...;
  // TEST_DATABASE_URL=postgresql+psycopg://localhost/embroidery_genie_test
  // pytest") and conftest.py:26-28 (reads TEST_DATABASE_URL, mirrors it
  // into DATABASE_URL). requiresEnv uses the exact name conftest.py reads
  // at conftest.py:26.
  //
  // Representation choice: a separate command was used instead of folding
  // this into a notes caveat on the required entry above. A notes-only
  // representation would be honest about what did NOT run, but it would
  // also mean the DB-backed tests are never actually exercised even when a
  // real database is available — the "conditional" tier would exist in the
  // interface but never do anything. Re-invoking the same command with the
  // env var present is the only way to turn a documented internal skip
  // into a real execution: without the var the runner must not invoke this
  // entry at all (SKIP, never PASS); with it, pytest fails for real
  // failures exactly as the required entry does.
  conditional: [
    {
      name: 'pytest (API/DB integration tests, requires a real PostgreSQL database)',
      command: 'pytest',
      args: [],
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
    'Not executed: authored by reading files only, no command in this ' +
      'spec has been run.',
    'requirements.txt:41 pins pytest==9.0.2; no other test runner is ' +
      'referenced anywhere under backend/.',
    'tests/test_engine.py has no reference to requires_db or app_client ' +
      '(checked), so it is unconditionally covered by the required tier.',
    'tests/test_api.py:15,17; tests/test_ai_cost_controls.py:18,26-27; ' +
      'and tests/test_hardening.py:14 each use requires_db or check ' +
      'TEST_DATABASE_URL directly, confirming DB-gating is enforced at ' +
      'the test level, not the file level, per conftest.py:1-9.',
    'Timeouts are unmeasured estimates: install pulls numpy, ' +
      'opencv-python-headless, pillow, pillow-heif and shapely; verify ' +
      'runs the full local test suite once.',
  ],
};
