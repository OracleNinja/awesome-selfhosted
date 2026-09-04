'use strict';

// Verification spec for ornith-cloud — the Ornith Cloud Gateway backend.
//
// This file is declarative only: it describes commands derived from
// repository evidence. It performs no filesystem mutation, no git
// operations, and spawns no child processes itself — the Lead's runner
// executes what is described here.
//
// Evidence:
//   - ornith-cloud/package-lock.json exists, so `npm ci` (not `npm install`)
//     is the correct, reproducible install command for a lockfile-backed
//     project.
//   - ornith-cloud/package.json:10  "build": "tsc -p tsconfig.json"
//   - ornith-cloud/package.json:12  "typecheck": "tsc --noEmit"
//   - ornith-cloud/package.json:13  "test": "vitest run"
//   - ornith-cloud/README.md:45-49  "Run" section lists exactly
//     `npm install`, `npm run typecheck`, `npm test` (then `npm run dev`)
//     as the intended verification sequence.
//   - All six files under ornith-cloud/tests/ (auth, gateway, classify,
//     search, prompt, cache) exercise pure functions or inject fakes.
//     tests/gateway.test.ts:8-14 builds its own Config via loadConfig()
//     with a hardcoded token and AI_PROVIDER: 'stub' instead of reading a
//     real .env, so `npm test` needs no ANTHROPIC_API_KEY, SEARCH_API_KEY,
//     or ORNITH_GATEWAY_TOKEN from the real environment. No conditional
//     entry is justified by this evidence.
//   - package.json's "scripts" (lines 8-15) define no lint/docker/e2e
//     script, and no eslint config file exists under ornith-cloud/, so no
//     optional entry is justified either — the empty array reflects the
//     evidence, not an oversight.

module.exports = {
  id: 'ornith-cloud',
  title: 'Ornith Cloud Gateway',
  dir: 'ornith-cloud',
  install: {
    command: 'npm',
    args: ['ci'],
    timeoutMs: 300000,
    count: {
      strategy: 'none',
      reason: 'npm ci only installs dependencies from package-lock.json; it runs no tests.',
    },
  },
  verify: [
    {
      name: 'typecheck',
      command: 'npm',
      args: ['run', 'typecheck'],
      timeoutMs: 60000,
      count: {
        strategy: 'none',
        reason: 'package.json:12 "typecheck": "tsc --noEmit" is a type check, not a test runner.',
      },
    },
    {
      name: 'build',
      command: 'npm',
      args: ['run', 'build'],
      timeoutMs: 60000,
      count: {
        strategy: 'none',
        reason: 'package.json:10 "build": "tsc -p tsconfig.json" compiles the project; it is not a test runner.',
      },
    },
    {
      name: 'test',
      command: 'npm',
      args: ['run', 'test', '--'],
      timeoutMs: 120000,
      count: { strategy: 'vitest-json' },
    },
  ],
  optional: [],
  conditional: [],
  notes: [
    'Install: ornith-cloud/package-lock.json is present, so `npm ci` is used ' +
      'instead of `npm install` for a reproducible install.',
    'typecheck: package.json:12 defines "typecheck": "tsc --noEmit".',
    'build: package.json:10 defines "build": "tsc -p tsconfig.json".',
    'test: package.json:13 defines "test": "vitest run".',
    'README.md:45-49 "Run" section lists npm install, npm run typecheck, ' +
      'npm test as the intended verification sequence.',
    'optional/conditional are empty: no lint/docker/e2e script exists in ' +
      'package.json, and every tests/*.test.ts file uses fakes/mocks or ' +
      'loadConfig() overrides (see tests/gateway.test.ts:8-14) rather than ' +
      'real secrets, so nothing qualifies as genuinely optional or ' +
      'env-gated per repository evidence.',
    'count: install (npm ci), typecheck (package.json:12 "tsc --noEmit"), ' +
      'and build (package.json:10 "tsc -p tsconfig.json") are declared ' +
      '`strategy: \'none\'` because none of them is a test runner — there is ' +
      'no artifact to count. test is declared `strategy: \'vitest-json\'` ' +
      'because package.json:13 defines "test": "vitest run", and vitest run ' +
      'accepts the runner-appended --reporter=default --reporter=json ' +
      '--outputFile=<path> flags directly. Because those flags are appended ' +
      'to the END of the step\'s args, and `npm run test <flags>` swallows ' +
      'trailing flags itself instead of forwarding them to the "test" ' +
      'script unless the arg list already contains a `--` separator, the ' +
      'test step\'s args were changed from [\'run\', \'test\'] to ' +
      '[\'run\', \'test\', \'--\'] so the appended flags land on vitest, ' +
      'not on npm. With no extra args appended, `npm run test --` still ' +
      'runs exactly `vitest run` (a trailing, argument-less `--` is a ' +
      'no-op for npm), so the same tests still run either way.',
    'Not verified: authored with no Bash access — npm ci, typecheck, ' +
      'build, and test were not executed, and the args change (adding ' +
      '`--`) and the count strategies were not exercised against the ' +
      'actual runner.',
  ],
};
