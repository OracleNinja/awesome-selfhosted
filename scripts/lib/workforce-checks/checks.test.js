'use strict';

/**
 * checks.test.js — standalone test suite for the workforce-checks modules
 * (git-checks.js, config-checks.js, state-checks.js). Run: node checks.test.js
 * Exits 0 if all assertions pass, non-zero otherwise. All fixtures are built
 * under os.tmpdir() and removed before exit, even on a mid-construction
 * throw, since fixture setup and assertions share one try/finally below.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const gitChecks = require('./git-checks.js');
const configChecks = require('./config-checks.js');
const stateChecks = require('./state-checks.js');

let passed = 0;
let failed = 0;

function check(name, condition, detail) {
  if (condition) {
    passed += 1;
  } else {
    failed += 1;
    console.error(`FAIL: ${name}${detail ? ` -- ${detail}` : ''}`);
  }
}

function byId(results, id) {
  return Array.isArray(results) ? results.find((r) => r && r.id === id) : undefined;
}

function statusOf(results, id) {
  const r = byId(results, id);
  return r ? r.status : undefined;
}

function detailOf(results, id) {
  const r = byId(results, id);
  return r ? r.detail : undefined;
}

// A pid certainly not running (Lead's hint: high numbers, confirmed dead
// via process.kill(pid, 0) throwing ESRCH). Runs before tmpRoot exists.
function findDeadPid() {
  for (let candidate = 999999; candidate > 999900; candidate -= 1) {
    try {
      process.kill(candidate, 0);
    } catch (err) {
      if (err && err.code === 'ESRCH') return candidate;
    }
  }
  throw new Error('could not find an unused pid for fixtures');
}

const DEAD_PID = findDeadPid();
const SQLITE_HEADER = Buffer.from('SQLite format 3\0', 'ascii');
const VALID_STATUSES = new Set(['PASS', 'WARN', 'FAIL']);

// Pure helpers: no disk I/O until called, and all calls happen inside the
// try/finally below, so defining them here is cleanup-safe.
function mkdirp(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeJson(p, data) {
  mkdirp(path.dirname(p));
  fs.writeFileSync(p, JSON.stringify(data));
}

function writeFile(p, content) {
  mkdirp(path.dirname(p));
  fs.writeFileSync(p, content);
}

function assertShape(moduleName, results) {
  check(`${moduleName}: run() returns an array`, Array.isArray(results));
  if (!Array.isArray(results)) return;
  results.forEach((r, i) => {
    const label = `${moduleName}[${i}]`;
    check(`${label}.id is a string`, Boolean(r) && typeof r.id === 'string', JSON.stringify(r));
    check(`${label}.title is a string`, Boolean(r) && typeof r.title === 'string', JSON.stringify(r));
    check(`${label}.status is a string`, Boolean(r) && typeof r.status === 'string', JSON.stringify(r));
    check(`${label}.status is PASS/WARN/FAIL`, Boolean(r) && VALID_STATUSES.has(r.status), JSON.stringify(r));
    check(`${label}.detail is a string`, Boolean(r) && typeof r.detail === 'string', JSON.stringify(r));
  });
}

function runNoThrow(mod, ctx, label) {
  try {
    const results = mod.run(ctx);
    check(`${label}: run() does not throw`, true);
    return results;
  } catch (err) {
    check(`${label}: run() does not throw`, false, err && err.message);
    return [];
  }
}

// Close over `tmpRoot` but only read it when called, inside the try below.
function makeGitRepo(name) {
  const root = path.join(tmpRoot, name);
  mkdirp(path.join(root, '.git'));
  return root;
}

function goodAgentMd() {
  return '---\ntools: Read, Grep, Glob, Edit, Write\n---\nbody\n';
}

function makeStateDb(name, headerOk, size, mode) {
  const dbPath = path.join(tmpRoot, `${name}.db`);
  const body = headerOk ? SQLITE_HEADER : Buffer.from('NOT A SQLITE HEADER!!');
  const padding = Buffer.alloc(Math.max(0, size - body.length), 0);
  fs.writeFileSync(dbPath, Buffer.concat([body, padding]));
  fs.chmodSync(dbPath, mode);
  return dbPath;
}

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'workforce-checks-test-'));

// Fixture construction and assertions both live in this try/finally: a
// throw anywhere here still reaches finally (AC8) and still reaches the
// Results line after it (AC3), since check() records failures without
// throwing.
try {
  // Stop git's upward repo search at tmpRoot's parent so a fake `.git`
  // can't be shadowed by a real ancestor repo; non-repo fixtures below
  // degrade to FAIL by construction, not by assumption.
  process.env.GIT_CEILING_DIRECTORIES = path.dirname(tmpRoot);

  // Fixtures below have a `.git` that is NOT a real repo (no `git init`,
  // no Bash). Git subcommands against them fail, so target-clean /
  // worktree-inventory / worker-base-staleness hit degraded branches
  // regardless of whether git is installed; integration-lock and
  // cherry-pick-in-progress never shell out, driven by files directly.
  const gitCleanRoot = makeGitRepo('git-clean');
  const gitStaleRoot = makeGitRepo('git-stale');
  writeJson(path.join(gitStaleRoot, '.git', 'ecc-integrate.lock'), {
    token: 'x', pid: DEAD_PID, hostname: os.hostname(), purpose: 't', startedAt: new Date().toISOString(),
  });
  writeFile(path.join(gitStaleRoot, '.git', 'CHERRY_PICK_HEAD'), `${'a'.repeat(40)}\n`);

  const gitAliveRoot = makeGitRepo('git-alive');
  writeJson(path.join(gitAliveRoot, '.git', 'ecc-integrate.lock'), {
    token: 'x', pid: process.pid, hostname: os.hostname(), purpose: 't', startedAt: new Date().toISOString(),
  });

  // -- config-checks fixtures ------------------------------------------------

  const configGood = path.join(tmpRoot, 'config-good');
  const goodHookPath = path.join(configGood, 'hooks', 'pre-worktree-confinement.js');
  writeFile(goodHookPath, '// stub\n');
  writeJson(path.join(configGood, 'settings.json'), {
    hooks: {
      PreToolUse: [{
        matcher: 'Edit|Write|MultiEdit|NotebookEdit',
        hooks: [{ command: `node ${goodHookPath} ecc-worker` }],
      }],
    },
  });
  writeFile(path.join(configGood, 'agents', 'ecc-worker.md'), goodAgentMd());
  writeFile(path.join(configGood, 'scripts', 'ecc-integrate.js'), '// stub\n');
  writeFile(path.join(configGood, 'scripts', 'ecc-worker-base.js'), '// stub\n');

  // No agents/ or scripts/ dirs here -> those checks fail on missing files.
  const configBad = path.join(tmpRoot, 'config-bad');
  writeFile(path.join(configBad, 'settings.json'), '{ not valid json');

  const configMissingHook = path.join(tmpRoot, 'config-missing-hook');
  writeJson(path.join(configMissingHook, 'settings.json'), { hooks: { PreToolUse: [] } });

  // -- state-checks fixtures --------------------------------------------------

  const stateGoodPath = makeStateDb('state-good', true, 4096, 0o600);

  const stateBadPath = makeStateDb('state-bad', false, 64, 0o644);
  writeJson(path.join(tmpRoot, '.state-bad.db.lock'), {
    token: 'x', pid: DEAD_PID, hostname: os.hostname(), startedAt: new Date().toISOString(),
  });

  const stateWarnPath = makeStateDb('state-warn', true, 4096, 0o600);
  writeJson(path.join(tmpRoot, '.state-warn.db.lock'), {
    token: 'x', pid: process.pid, hostname: os.hostname(), startedAt: new Date().toISOString(),
  });

  const stateMissingPath = path.join(tmpRoot, 'does-not-exist.db');

  // -- git-checks --------------------------------------------------------------

  const gitClean = gitChecks.run({ repoRoot: gitCleanRoot });
  assertShape('git-checks(clean)', gitClean);
  check('git-checks(clean): integration-lock PASS (no lock file)', statusOf(gitClean, 'integration-lock') === 'PASS');
  check('git-checks(clean): cherry-pick-in-progress PASS (no CHERRY_PICK_HEAD)', statusOf(gitClean, 'cherry-pick-in-progress') === 'PASS');
  // Pins known-questionable behaviour (not an endorsement): git-checks.js's
  // author flags worktree-inventory as "deliberately always PASS ... even
  // on enumeration trouble". Documented here, not assumed correct.
  check('git-checks(clean): worktree-inventory PASS (informational, never fails)', statusOf(gitClean, 'worktree-inventory') === 'PASS');
  check('git-checks(clean): target-clean FAIL (fixture is not a real git repo)', statusOf(gitClean, 'target-clean') === 'FAIL');
  check('git-checks(clean): worker-base-staleness FAIL (cannot enumerate worktrees)', statusOf(gitClean, 'worker-base-staleness') === 'FAIL');

  const gitStale = gitChecks.run({ repoRoot: gitStaleRoot });
  assertShape('git-checks(stale)', gitStale);
  check('git-checks(stale): integration-lock FAIL (stale lock, dead pid)', statusOf(gitStale, 'integration-lock') === 'FAIL');
  check('git-checks(stale): cherry-pick-in-progress FAIL (CHERRY_PICK_HEAD present)', statusOf(gitStale, 'cherry-pick-in-progress') === 'FAIL');
  const staleLockDetail = detailOf(gitStale, 'integration-lock');
  check(
    'git-checks(stale): integration-lock FAIL detail names the actual dead pid, not a generic message',
    typeof staleLockDetail === 'string' && staleLockDetail.includes(String(DEAD_PID)) && staleLockDetail.toLowerCase().includes('stale'),
    staleLockDetail,
  );

  const gitAlive = gitChecks.run({ repoRoot: gitAliveRoot });
  assertShape('git-checks(alive)', gitAlive);
  check('git-checks(alive): integration-lock WARN (live pid holds lock)', statusOf(gitAlive, 'integration-lock') === 'WARN');

  // -- config-checks -------------------------------------------------------------

  const configGoodResults = configChecks.run({ claudeHome: configGood });
  assertShape('config-checks(good)', configGoodResults);
  check('config-checks(good): settings-parseable PASS', statusOf(configGoodResults, 'settings-parseable') === 'PASS');
  check('config-checks(good): confinement-hook-registered PASS', statusOf(configGoodResults, 'confinement-hook-registered') === 'PASS');
  check('config-checks(good): confinement-hook-present PASS', statusOf(configGoodResults, 'confinement-hook-present') === 'PASS');
  check('config-checks(good): worker-grants-no-bash PASS', statusOf(configGoodResults, 'worker-grants-no-bash') === 'PASS');
  check('config-checks(good): gate-tools-present PASS', statusOf(configGoodResults, 'gate-tools-present') === 'PASS');

  const configBadResults = configChecks.run({ claudeHome: configBad });
  assertShape('config-checks(bad)', configBadResults);
  check('config-checks(bad): settings-parseable FAIL (malformed JSON)', statusOf(configBadResults, 'settings-parseable') === 'FAIL');
  check('config-checks(bad): confinement-hook-registered FAIL (settings unreadable)', statusOf(configBadResults, 'confinement-hook-registered') === 'FAIL');
  check('config-checks(bad): worker-grants-no-bash FAIL (agent file missing)', statusOf(configBadResults, 'worker-grants-no-bash') === 'FAIL');
  check('config-checks(bad): gate-tools-present FAIL (scripts missing)', statusOf(configBadResults, 'gate-tools-present') === 'FAIL');

  const configMissingHookResults = configChecks.run({ claudeHome: configMissingHook });
  assertShape('config-checks(missing-hook)', configMissingHookResults);
  check('config-checks(missing-hook): settings-parseable PASS', statusOf(configMissingHookResults, 'settings-parseable') === 'PASS');
  check('config-checks(missing-hook): confinement-hook-registered FAIL (no PreToolUse entry)', statusOf(configMissingHookResults, 'confinement-hook-registered') === 'FAIL');
  const missingHookDetail = detailOf(configMissingHookResults, 'confinement-hook-registered');
  check(
    'config-checks(missing-hook): confinement-hook-registered FAIL detail names the confinement script, not a generic message',
    typeof missingHookDetail === 'string' && missingHookDetail.includes('pre-worktree-confinement.js'),
    missingHookDetail,
  );

  // -- state-checks ----------------------------------------------------------------

  const stateGoodResults = stateChecks.run({ stateDbPath: stateGoodPath });
  assertShape('state-checks(good)', stateGoodResults);
  check('state-checks(good): state-db-present PASS', statusOf(stateGoodResults, 'state-db-present') === 'PASS');
  check('state-checks(good): state-db-header PASS', statusOf(stateGoodResults, 'state-db-header') === 'PASS');
  check('state-checks(good): state-db-permissions PASS', statusOf(stateGoodResults, 'state-db-permissions') === 'PASS');
  check('state-checks(good): state-store-lock PASS (no lock file)', statusOf(stateGoodResults, 'state-store-lock') === 'PASS');
  check('state-checks(good): state-db-freshness PASS', statusOf(stateGoodResults, 'state-db-freshness') === 'PASS');

  const stateBadResults = stateChecks.run({ stateDbPath: stateBadPath });
  assertShape('state-checks(bad)', stateBadResults);
  check('state-checks(bad): state-db-header FAIL (wrong magic bytes)', statusOf(stateBadResults, 'state-db-header') === 'FAIL');
  check('state-checks(bad): state-db-permissions WARN (mode 0644)', statusOf(stateBadResults, 'state-db-permissions') === 'WARN');
  check('state-checks(bad): state-store-lock FAIL (stale lock, dead pid)', statusOf(stateBadResults, 'state-store-lock') === 'FAIL');
  const stateStaleLockDetail = detailOf(stateBadResults, 'state-store-lock');
  check(
    'state-checks(bad): state-store-lock FAIL detail names the actual dead pid, not a generic message',
    typeof stateStaleLockDetail === 'string' && stateStaleLockDetail.includes(String(DEAD_PID)),
    stateStaleLockDetail,
  );

  const stateWarnResults = stateChecks.run({ stateDbPath: stateWarnPath });
  assertShape('state-checks(warn)', stateWarnResults);
  check('state-checks(warn): state-store-lock WARN (live pid holds lock)', statusOf(stateWarnResults, 'state-store-lock') === 'WARN');

  const stateMissingResults = stateChecks.run({ stateDbPath: stateMissingPath });
  assertShape('state-checks(missing)', stateMissingResults);
  check('state-checks(missing): state-db-present FAIL (does not exist)', statusOf(stateMissingResults, 'state-db-present') === 'FAIL');

  // -- robustness: run(ctx) must never throw ---------------------------------------

  const nonexistentCtx = {
    repoRoot: path.join(tmpRoot, 'does-not-exist-repo'),
    claudeHome: path.join(tmpRoot, 'does-not-exist-home'),
    eccRoot: path.join(tmpRoot, 'does-not-exist-ecc'),
    stateDbPath: path.join(tmpRoot, 'does-not-exist-nested', 'db'),
  };
  assertShape('git-checks(nonexistent ctx)', runNoThrow(gitChecks, nonexistentCtx, 'git-checks(nonexistent ctx)'));
  assertShape('config-checks(nonexistent ctx)', runNoThrow(configChecks, nonexistentCtx, 'config-checks(nonexistent ctx)'));
  assertShape('state-checks(nonexistent ctx)', runNoThrow(stateChecks, nonexistentCtx, 'state-checks(nonexistent ctx)'));

  assertShape('git-checks(empty ctx)', runNoThrow(gitChecks, {}, 'git-checks(empty ctx)'));
  assertShape('config-checks(empty ctx)', runNoThrow(configChecks, {}, 'config-checks(empty ctx)'));
  assertShape('state-checks(empty ctx)', runNoThrow(stateChecks, {}, 'state-checks(empty ctx)'));
} finally {
  fs.rmSync(tmpRoot, { recursive: true, force: true });
}

console.log(`Results: Passed: ${passed}, Failed: ${failed}`);
process.exit(failed > 0 ? 1 : 0);
