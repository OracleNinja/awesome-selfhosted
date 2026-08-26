'use strict';

// Declarative verification spec for ornith-desktop.
// This file only describes commands derived from repository evidence
// (see `notes` below for exact citations); it never executes anything.

module.exports = {
  id: 'ornith-desktop',
  title: 'Ornith Desktop (Electron chat client)',
  dir: 'ornith-desktop',

  install: {
    command: 'npm',
    args: ['ci'],
    // ornith-desktop/package-lock.json exists and is in sync with
    // package.json, so `npm ci` is available and correct here. Unlike
    // `npm install`, `npm ci` never rewrites the lockfile — required
    // because a verification run must not mutate tracked repository
    // content. Electron ^39.2.6 and @playwright/test ^1.62.1 are large
    // downloads regardless of install command (package.json:39,33;
    // SPEC.md:55 cites ~150 MB installed for Electron alone).
    timeoutMs: 900000,
    // `npm ci` installs dependencies; it runs no tests and writes no
    // test-result artifact of any kind.
    count: { strategy: 'none', reason: 'dependency install, not a test runner' },
  },

  verify: [
    {
      // package.json:12 "build": "node scripts/build.mjs". Required
      // because tests/e2e/app.spec.ts:34-36 launches Electron against
      // appRoot, which resolves via package.json:8 main
      // "dist-electron/main.js" — that file only exists after a build.
      name: 'build',
      command: 'npm',
      args: ['run', 'build'],
      timeoutMs: 180000,
      // scripts/build.mjs (package.json:12) compiles renderer + electron
      // output; it produces build artifacts, not test results.
      count: { strategy: 'none', reason: 'build step, not a test runner' },
    },
    {
      // First half of package.json:13 typecheck script (renderer +
      // shared code, default tsconfig.json).
      name: 'typecheck-renderer',
      command: 'npx',
      args: ['tsc', '--noEmit'],
      timeoutMs: 120000,
      // tsc --noEmit type-checks only; it has no notion of tests and
      // writes no result artifact.
      count: { strategy: 'none', reason: 'typecheck, not a test runner' },
    },
    {
      // Second half of package.json:13: "tsc --noEmit -p
      // tsconfig.electron.json" (file confirmed present).
      name: 'typecheck-electron',
      command: 'npx',
      args: ['tsc', '--noEmit', '-p', 'tsconfig.electron.json'],
      timeoutMs: 120000,
      // Same tool as typecheck-renderer, different project file; still
      // no test concept, no result artifact.
      count: { strategy: 'none', reason: 'typecheck, not a test runner' },
    },
    {
      // package.json:14 "lint": "eslint .". Plain Node, no Electron
      // runtime needed.
      name: 'lint',
      command: 'npx',
      args: ['eslint', '.'],
      timeoutMs: 120000,
      // eslint . (package.json:14) reports lint violations, not test
      // counts; no machine-readable test artifact exists.
      count: { strategy: 'none', reason: 'lint, not a test runner' },
    },
    {
      // package.json:15 "test": "vitest run"; vitest.config.ts:6 include
      // globs list tests/unit/**/*.test.ts and
      // tests/integration/**/*.test.ts together. Split by directory here
      // for clearer PASS/FAIL attribution. environment: 'node'
      // (vitest.config.ts:7) — no browser/Electron involved.
      name: 'unit',
      command: 'npx',
      args: ['vitest', 'run', 'tests/unit'],
      timeoutMs: 60000,
      // Invokes vitest directly via npx (no npm-script wrapper), so the
      // runner's appended --reporter/--outputFile flags reach the vitest
      // CLI unmodified.
      count: { strategy: 'vitest-json' },
    },
    {
      // Same vitest config; tests/integration/client.test.ts and
      // orchestrator.test.ts exercise a stub Ollama server
      // (tests/integration/stubOllama.ts) in-process — no real network
      // dependency, no Electron. Plain Node, required.
      name: 'integration',
      command: 'npx',
      args: ['vitest', 'run', 'tests/integration'],
      timeoutMs: 60000,
      // Same tool, same direct npx invocation as unit — no wrapper in
      // the way of appended flags.
      count: { strategy: 'vitest-json' },
    },
  ],

  optional: [
    {
      // package.json:18 "test:e2e": "playwright test";
      // playwright.config.ts:4 testDir './tests/e2e' covers app.spec.ts
      // and performance.spec.ts. tests/e2e/app.spec.ts:34-41 launches a
      // real Electron binary via _electron.launch(), passing
      // '--no-sandbox'/'--disable-gpu' with a comment: "Chromium's
      // setuid sandbox is unavailable in this container" — a genuine GUI
      // process launch of a macOS-targeted app (README.md:3, SPEC.md:21),
      // not a mock.
      name: 'e2e-electron',
      command: 'npx',
      args: ['playwright', 'test'],
      reason:
        "README.md:34 states Linux CI needs a virtual display: " +
        "'run the E2E suite under a virtual display: xvfb-run -a npm " +
        "run test:e2e'. Xvfb is a system package, not an npm dependency, " +
        "and is not guaranteed present in a headless verification " +
        "container; Electron additionally needs GTK/NSS/ATK/Mesa system " +
        "libraries npm install does not provide. Cannot be assumed to " +
        "run here — report SKIP, never PASS, if it does not.",
      // Invokes playwright directly via npx (no npm-script wrapper);
      // playwright.config.ts:11 sets reporter: [['list']] as a config
      // default, but a CLI --reporter flag overrides it, so the
      // runner's appended --reporter=list,json still lands as intended.
      count: { strategy: 'playwright-json' },
    },
  ],

  conditional: [],

  notes: [
    'ornith-desktop/package-lock.json is present and lockfile-in-sync (npm ci succeeds against it), so install uses `npm ci` rather than `npm install`: a verification run must not mutate tracked repository content, and `npm install` was observed to rewrite package-lock.json by one line where `npm ci` leaves it byte-identical.',
    'package.json:15 "test": "vitest run"; vitest.config.ts:6 include globs cover tests/unit/**/*.test.ts and tests/integration/**/*.test.ts — split into two verify entries above for clearer reporting.',
    'package.json:13 "typecheck" chains two tsc invocations; split into typecheck-renderer (tsconfig.json) and typecheck-electron (tsconfig.electron.json, confirmed present via glob).',
    'package.json:8 main "dist-electron/main.js" is produced by package.json:12 "build": "node scripts/build.mjs" — added as a required verify step because tests/e2e/app.spec.ts:34-36 loads appRoot via Electron, which resolves its entry point from package.json main.',
    'vitest.config.ts:5 comment: "Playwright drives Electron itself and must not be collected by vitest" — confirms unit/integration vs e2e are deliberately separate tooling, matching the required/optional split here.',
    'All non-install commands invoke local devDependency binaries via npx (typescript ^5.9.3 package.json:44, eslint ^10.8.1 package.json:42, vitest ^3.2.7 package.json:47, @playwright/test ^1.62.1 package.json:33) rather than assuming global installs.',
    'No conditional checks: nothing here genuinely depends on an optional env var — tests/integration and tests/e2e both use a stub Ollama server (tests/integration/stubOllama.ts), never a real Ollama instance, so there is no "runs only if X is configured" case to encode.',
    'Not established: whether this verification container has network egress sufficient for the Electron download (SPEC.md:55: ~150 MB installed), or whether Xvfb / GTK-NSS-ATK-Mesa libraries needed for the optional e2e step are present.',
    'count strategies: unit and integration both run `npx vitest run <dir>` directly (package.json:15) with no npm-script wrapper between the spec and the binary, so the runner\'s appended --reporter/--outputFile flags reach vitest unmodified — both use count: { strategy: \'vitest-json\' }. e2e-electron runs `npx playwright test` directly (package.json:18); playwright.config.ts:11 sets reporter: [[\'list\']] but a CLI --reporter flag overrides a config default, so the runner\'s appended --reporter=list,json plus PLAYWRIGHT_JSON_OUTPUT_NAME still take effect — count: { strategy: \'playwright-json\' }. install (npm ci), build (node scripts/build.mjs, package.json:12), typecheck-renderer/typecheck-electron (tsc --noEmit, package.json:13), and lint (eslint ., package.json:14) are not test runners and produce no test-result artifact of any kind, so each declares count: { strategy: \'none\', reason: ... } rather than rendering as UNKNOWN.',
  ],
};
