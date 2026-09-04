#!/usr/bin/env node
/**
 * run.js — execute the declarative verification specs in this directory.
 *
 * Each product declares how it is installed and verified; this runner executes
 * those declarations. The specs are written by people (or agents) who cannot
 * run anything, so the runner is the only thing that turns a declaration into
 * evidence.
 *
 * The semantics that matter, and why:
 *
 *   PASS  a required step ran and succeeded.
 *   FAIL  a required step ran and failed, or installation failed. Exits non-zero.
 *   SKIP  a step was deliberately not run — an optional step without
 *         --with-optional, or a conditional step whose environment variable is
 *         absent. A SKIP is never reported as a PASS: not running something is
 *         not evidence that it works.
 *   WARN  the run completed but verified less than it appears to — nothing was
 *         declared required, or installation was bypassed.
 *
 * An optional step, when actually requested with --with-optional, is graded
 * PASS/FAIL like anything else. Downgrading a real failure to SKIP would hide
 * exactly the information the harness exists to surface.
 *
 * Usage:
 *   node tools/verify/run.js <id|all> [<id> ...] [--with-optional] [--skip-install] [--json]
 */

'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// Both are overridable so the runner's own test suite can point it at
// deterministic fixture specs and exercise real execution, rather than
// asserting about behaviour by reading this file's source.
const VERIFY_DIR = process.env.VERIFY_SPEC_DIR
  ? path.resolve(process.env.VERIFY_SPEC_DIR)
  : __dirname;
const REPO_ROOT = process.env.VERIFY_REPO_ROOT
  ? path.resolve(process.env.VERIFY_REPO_ROOT)
  : path.resolve(__dirname, '..', '..');
const DEFAULT_TIMEOUT_MS = 600000;

function discoverSpecs() {
  return fs.readdirSync(VERIFY_DIR)
    .filter(f => f.endsWith('.js') && f !== 'run.js' && !f.endsWith('.test.js'))
    .map(f => path.join(VERIFY_DIR, f));
}

function parseArgs(argv) {
  const out = { ids: [], withOptional: false, skipInstall: false, json: false };
  for (const a of argv.slice(2)) {
    if (a === '--with-optional') out.withOptional = true;
    else if (a === '--skip-install') out.skipInstall = true;
    else if (a === '--json') out.json = true;
    else if (a.startsWith('--')) { throw new Error(`Unknown option: ${a}`); }
    else out.ids.push(a);
  }
  return out;
}

/** Steps need checking too, not just the spec around them. A step with no
 *  command crashed the runner outright — spawnSync rejects a non-string —
 *  and a step with no name printed "PASS undefined", both while the
 *  file-level checks below happily reported the spec as well-formed. */
function stepProblems(step, key, index) {
  const at = `${key}[${index}]`;
  if (!step || typeof step !== 'object') return [`${at} is not an object`];
  const problems = [];
  if (typeof step.name !== 'string' || !step.name) problems.push(`${at} is missing name`);
  if (typeof step.command !== 'string' || !step.command) problems.push(`${at} is missing command`);
  if (step.args !== undefined) {
    if (!Array.isArray(step.args)) problems.push(`${at} args is not an array`);
    else if (step.args.some(a => typeof a !== 'string')) problems.push(`${at} args contains a non-string`);
  }
  return problems;
}

/** A malformed spec is a reportable failure, never a crash. */
function loadSpec(file) {
  let spec;
  try {
    spec = require(file);
  } catch (error) {
    return { error: `could not load ${path.basename(file)}: ${error.message}` };
  }
  const problems = [];
  if (!spec || typeof spec !== 'object') problems.push('module.exports is not an object');
  else {
    if (typeof spec.id !== 'string' || !spec.id) problems.push('missing id');
    if (typeof spec.dir !== 'string' || !spec.dir) problems.push('missing dir');
    for (const key of ['verify', 'optional', 'conditional']) {
      if (spec[key] === undefined) continue;
      if (!Array.isArray(spec[key])) { problems.push(`${key} is not an array`); continue; }
      spec[key].forEach((step, i) => problems.push(...stepProblems(step, key, i)));
    }
    if (spec.install !== undefined) {
      if (!spec.install || typeof spec.install !== 'object') problems.push('install is not an object');
      else {
        // install is named by the caller, so it needs a command but not a name.
        if (typeof spec.install.command !== 'string' || !spec.install.command) problems.push('install is missing command');
        if (spec.install.args !== undefined && !Array.isArray(spec.install.args)) problems.push('install args is not an array');
      }
    }
  }
  if (problems.length) return { error: `${path.basename(file)}: ${problems.join('; ')}` };
  return { spec };
}

/**
 * Test counts.
 *
 * A green run that says nothing about how much ran is the failure this section
 * exists to prevent: a suite can shrink from hundreds of tests to three and
 * still exit 0. But a count that is wrong is worse than no count, so every
 * number here comes from a machine-readable artifact the test runner writes
 * itself. Nothing scrapes a human-readable summary line, because those are
 * written for people and change without warning.
 *
 * When an artifact is missing, unparseable, or absent of totals, the count is
 * UNKNOWN. It is never zero — "no evidence of tests" and "evidence of no
 * tests" are different claims, and only the second one is a number.
 *
 * Artifacts are written under the OS temp directory, never inside the project,
 * so counting can never trip the working-tree guard.
 */
const COUNT_STRATEGIES = {
  // pytest reads PYTEST_ADDOPTS from the environment, so a count costs no
  // change to the declared command at all.
  'junit-xml': {
    prepare: (file) => ({ env: { PYTEST_ADDOPTS: `--junitxml=${file}` }, appendArgs: [] }),
    parse(text) {
      let total = 0, skipped = 0, seen = false;
      // Match <testsuite ...> but not the <testsuites> wrapper around it.
      const re = /<testsuite\s[^>]*>/g;
      let m;
      while ((m = re.exec(text)) !== null) {
        const tests = /\btests="(\d+)"/.exec(m[0]);
        if (!tests) continue;
        seen = true;
        total += Number(tests[1]);
        const sk = /\bskipped="(\d+)"/.exec(m[0]);
        if (sk) skipped += Number(sk[1]);
      }
      return seen ? { total, skipped } : null;
    },
  },
  // The default reporter is kept alongside the JSON one so a failure is still
  // readable in the log; JSON alone would replace it.
  'vitest-json': {
    prepare: (file) => ({
      env: {},
      appendArgs: ['--reporter=default', '--reporter=json', `--outputFile=${file}`],
    }),
    parse(text) {
      const d = JSON.parse(text);
      if (typeof d.numTotalTests !== 'number') return null;
      return { total: d.numTotalTests, skipped: Number(d.numPendingTests) || 0 };
    },
  },
  'playwright-json': {
    prepare: (file) => ({
      env: { PLAYWRIGHT_JSON_OUTPUT_NAME: file },
      appendArgs: ['--reporter=list,json'],
    }),
    parse(text) {
      const stats = JSON.parse(text).stats;
      if (!stats) return null;
      const n = (v) => Number(v) || 0;
      return {
        total: n(stats.expected) + n(stats.unexpected) + n(stats.flaky) + n(stats.skipped),
        skipped: n(stats.skipped),
      };
    },
  },
};

/** `python -m pytest` and a bare `pytest` are countable without touching the
 *  command, so they are the one case worth inferring. Everything else must be
 *  declared: guessing at a command's ecosystem is how brittle counts start. */
function looksLikePytest(step) {
  if (/(^|\/)pytest$/.test(step.command || '')) return true;
  const args = step.args || [];
  const i = args.indexOf('-m');
  return i !== -1 && args[i + 1] === 'pytest' && /(^|\/)python[\d.]*$/.test(step.command || '');
}

function countPlanFor(step) {
  const declared = step.count;
  if (declared && typeof declared === 'object' && declared.strategy) {
    if (declared.strategy === 'none') {
      return { kind: 'none', reason: declared.reason || 'the spec declares this step reports no test count' };
    }
    const strategy = COUNT_STRATEGIES[declared.strategy];
    if (!strategy) return { kind: 'unknown', reason: `spec declares unknown count strategy "${declared.strategy}"` };
    return { kind: 'strategy', name: declared.strategy, strategy, declared: true };
  }
  if (looksLikePytest(step)) {
    return { kind: 'strategy', name: 'junit-xml', strategy: COUNT_STRATEGIES['junit-xml'], declared: false };
  }
  return { kind: 'unknown', reason: 'no count strategy declared, and none can be inferred without guessing' };
}

function readCount(plan, artifact) {
  if (plan.kind === 'none') return { known: false, notApplicable: true, reason: plan.reason };
  if (plan.kind === 'unknown') return { known: false, reason: plan.reason };
  let text;
  try {
    text = fs.readFileSync(artifact, 'utf8');
  } catch {
    return { known: false, reason: `${plan.name} wrote no artifact` };
  }
  let parsed;
  try {
    parsed = plan.strategy.parse(text);
  } catch (error) {
    return { known: false, reason: `${plan.name} artifact was unparseable: ${error.message}` };
  }
  if (!parsed) return { known: false, reason: `${plan.name} artifact carried no totals` };
  return {
    known: true,
    total: parsed.total,
    skipped: parsed.skipped,
    executed: parsed.total - parsed.skipped,
    source: plan.name,
    inferred: !plan.declared,
  };
}

function renderCount(count) {
  if (!count) return '';
  if (count.notApplicable) return `  tests n/a`;
  if (!count.known) return `  tests UNKNOWN`;
  if (count.skipped) return `  tests ${count.total} (${count.executed} ran, ${count.skipped} skipped)`;
  return `  tests ${count.total}`;
}

function runStep(cwd, step, tier) {
  const timeout = Number(step.timeoutMs) || DEFAULT_TIMEOUT_MS;
  const declaredArgs = step.args || [];
  const plan = countPlanFor(step);

  // Counting may add reporter flags or environment. Both are reported below
  // as the effective command, so what actually ran is never hidden behind
  // what was declared.
  let args = declaredArgs.slice();
  const env = { ...process.env };
  let artifactDir = null;
  let artifact = null;
  if (plan.kind === 'strategy') {
    artifactDir = fs.mkdtempSync(path.join(os.tmpdir(), 'verify-count-'));
    artifact = path.join(artifactDir, 'result');
    const prepared = plan.strategy.prepare(artifact);
    args = args.concat(prepared.appendArgs || []);
    for (const [key, value] of Object.entries(prepared.env || {})) {
      // PYTEST_ADDOPTS is additive: clobbering a caller's value would change
      // what the suite does, not just how it is measured.
      env[key] = key === 'PYTEST_ADDOPTS' && env[key] ? `${env[key]} ${value}` : value;
    }
  }

  const started = Date.now();
  process.stdout.write(`    ${tier} $ ${step.command} ${args.join(' ')}\n`);
  const r = spawnSync(step.command, args, {
    cwd, timeout, encoding: 'utf8', env, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const durationMs = Date.now() - started;
  const output = `${r.stdout || ''}${r.stderr || ''}`;
  const timedOut = Boolean(r.error && r.error.code === 'ETIMEDOUT');

  const count = readCount(plan, artifact);
  if (artifactDir) fs.rmSync(artifactDir, { recursive: true, force: true });

  return {
    name: step.name, tier,
    command: step.command, args, declaredArgs,
    argsWereExtended: args.length !== declaredArgs.length,
    exitCode: r.status, durationMs, timedOut,
    spawnError: r.error && !timedOut ? r.error.message : null,
    output, count,
  };
}

/** One line per executed step, carrying the evidence a reviewer needs:
 *  what ran, in which tier, how it exited, how long it took, how much ran. */
function stepLine(status, r) {
  const exit = r.timedOut ? 'TIMED OUT' : `exit ${r.exitCode}`;
  return `  ${status}  ${r.name}  [${r.tier}]  ${exit}  ${(r.durationMs / 1000).toFixed(1)}s${renderCount(r.count)}`;
}

function tail(output, lines = 25) {
  return output.trim().split('\n').slice(-lines).map(l => `      | ${l}`).join('\n');
}

function main() {
  let opts;
  try { opts = parseArgs(process.argv); } catch (e) { console.error(e.message); process.exit(2); }

  const files = discoverSpecs();
  const loaded = files.map(f => ({ file: f, ...loadSpec(f) }));
  const broken = loaded.filter(l => l.error);
  const specs = loaded.filter(l => l.spec).map(l => l.spec);

  const wanted = opts.ids.includes('all') || opts.ids.length === 0
    ? specs.map(s => s.id)
    : opts.ids;
  const unknown = wanted.filter(id => !specs.some(s => s.id === id));

  const report = { projects: [], brokenSpecs: broken.map(b => b.error), unknownIds: unknown };

  for (const id of wanted) {
    const spec = specs.find(s => s.id === id);
    if (!spec) continue;
    const cwd = path.resolve(REPO_ROOT, spec.dir);
    const entry = { id: spec.id, title: spec.title || spec.id, dir: spec.dir, steps: [], status: 'PASS' };
    console.log(`\n=== ${spec.title || spec.id}  (${spec.dir}) ===`);

    if (!fs.existsSync(cwd)) {
      entry.status = 'FAIL';
      entry.steps.push({ name: 'directory exists', status: 'FAIL', detail: `no such directory: ${cwd}` });
      console.log(`  FAIL  directory exists — no such directory: ${cwd}`);
      report.projects.push(entry);
      continue;
    }

    // ---- install ---------------------------------------------------------
    if (spec.install && !opts.skipInstall) {
      const r = runStep(cwd, { ...spec.install, name: 'install' }, 'install');
      const ok = r.exitCode === 0;
      entry.steps.push({ name: 'install', status: ok ? 'PASS' : 'FAIL', ...strip(r) });
      console.log(stepLine(ok ? 'PASS' : 'FAIL', r));
      if (!ok) {
        // An install failure is never hidden and never allows verification to
        // proceed: anything run afterwards would be testing the wrong thing.
        console.log(r.timedOut ? '      | TIMED OUT' : tail(r.output));
        entry.status = 'FAIL';
        entry.installFailed = true;
        report.projects.push(entry);
        continue;
      }
    } else if (spec.install && opts.skipInstall) {
      entry.steps.push({ name: 'install', status: 'SKIP', tier: 'install', detail: '--skip-install was passed' });
      if (entry.status === 'PASS') entry.status = 'WARN';
      console.log('  SKIP  install  [install]  not run — --skip-install was passed; results below assume an existing environment');
    }

    // ---- required --------------------------------------------------------
    const required = spec.verify || [];
    if (required.length === 0) {
      entry.steps.push({ name: 'required verification', status: 'WARN', tier: 'required', detail: 'this spec declares no required verification' });
      if (entry.status === 'PASS') entry.status = 'WARN';
      console.log('  WARN  required verification  [required]  this spec declares no required verification');
    }
    for (const step of required) {
      const r = runStep(cwd, step, 'required');
      const ok = r.exitCode === 0;
      entry.steps.push({ name: step.name, status: ok ? 'PASS' : 'FAIL', ...strip(r) });
      console.log(stepLine(ok ? 'PASS' : 'FAIL', r));
      if (!ok) {
        console.log(r.timedOut ? '      | TIMED OUT' : tail(r.output));
        entry.status = 'FAIL';
      }
    }

    // ---- optional --------------------------------------------------------
    for (const step of spec.optional || []) {
      if (!opts.withOptional) {
        entry.steps.push({ name: step.name, status: 'SKIP', tier: 'optional', detail: step.reason || 'optional; pass --with-optional to run' });
        console.log(`  SKIP  ${step.name}  [optional]  not run — ${step.reason || 'optional; pass --with-optional to run'}`);
        continue;
      }
      const r = runStep(cwd, step, 'optional');
      const ok = r.exitCode === 0;
      entry.steps.push({ name: step.name, status: ok ? 'PASS' : 'FAIL', optional: true, ...strip(r) });
      console.log(stepLine(ok ? 'PASS' : 'FAIL', r));
      if (!ok) { console.log(r.timedOut ? '      | TIMED OUT' : tail(r.output)); entry.status = 'FAIL'; }
    }

    // ---- conditional -----------------------------------------------------
    for (const step of spec.conditional || []) {
      const envName = step.requiresEnv;
      if (!envName || !process.env[envName]) {
        entry.steps.push({ name: step.name, status: 'SKIP', tier: 'conditional', detail: `requires ${envName || 'an unnamed environment variable'}; not set` });
        console.log(`  SKIP  ${step.name}  [conditional]  not run — requires ${envName || 'an unnamed environment variable'}, which is not set`);
        continue;
      }
      const r = runStep(cwd, step, 'conditional');
      const ok = r.exitCode === 0;
      entry.steps.push({ name: step.name, status: ok ? 'PASS' : 'FAIL', conditional: true, ...strip(r) });
      console.log(`${stepLine(ok ? 'PASS' : 'FAIL', r)}  (${envName} set)`);
      if (!ok) { console.log(r.timedOut ? '      | TIMED OUT' : tail(r.output)); entry.status = 'FAIL'; }
    }

    for (const n of spec.notes || []) console.log(`  note: ${n}`);
    report.projects.push(entry);
  }

  // ---- summary -----------------------------------------------------------
  const counts = { PASS: 0, FAIL: 0, SKIP: 0, WARN: 0 };
  for (const p of report.projects) for (const s of p.steps) counts[s.status] = (counts[s.status] || 0) + 1;

  console.log('\n=== summary ===');
  for (const p of report.projects) console.log(`  ${p.status.padEnd(4)}  ${p.id}`);
  for (const b of report.brokenSpecs) console.log(`  FAIL  unloadable spec — ${b}`);
  for (const u of report.unknownIds) console.log(`  FAIL  unknown project id: ${u}`);
  // How much actually ran. A step that executed but could not be counted is
  // reported as UNKNOWN and never folded into the total, because adding zero
  // for it would understate the run and adding nothing would overstate the
  // confidence. Both are visible here so a reviewer can weigh them.
  const tests = { ran: 0, skippedByRunner: 0, countedSteps: 0, unknownSteps: 0, noCountSteps: 0 };
  for (const p of report.projects) {
    for (const st of p.steps) {
      const c = st.count;
      if (!c) continue;
      if (c.known) {
        tests.countedSteps += 1;
        tests.ran += c.executed;
        tests.skippedByRunner += c.skipped || 0;
      } else if (c.notApplicable) {
        tests.noCountSteps += 1;
      } else {
        tests.unknownSteps += 1;
      }
    }
  }
  report.totals = { steps: counts, tests };

  console.log(`\n  steps: ${counts.PASS} pass, ${counts.FAIL} fail, ${counts.SKIP} skip, ${counts.WARN} warn`);
  console.log('  (SKIP means not run — it is not a pass)');

  const skippedNote = tests.skippedByRunner ? `, ${tests.skippedByRunner} skipped by the test runner` : '';
  console.log(`  tests: ${tests.ran} ran${skippedNote}, counted from ${tests.countedSteps} step(s)`);
  if (tests.unknownSteps) {
    console.log(`         ${tests.unknownSteps} executed step(s) report tests UNKNOWN — not counted, not assumed zero`);
  }
  if (tests.noCountSteps) {
    console.log(`         ${tests.noCountSteps} step(s) declare no test count (not test runners)`);
  }

  if (opts.json) console.log(`\n${JSON.stringify(report, null, 2)}`);

  const failed = report.projects.some(p => p.status === 'FAIL') || broken.length > 0 || unknown.length > 0;
  process.exitCode = failed ? 1 : 0;
}

/** Keep structured output small; full text is streamed above when it matters. */
function strip(r) {
  return {
    tier: r.tier,
    command: r.command, args: r.args, declaredArgs: r.declaredArgs,
    argsWereExtended: r.argsWereExtended,
    exitCode: r.exitCode, durationMs: r.durationMs, timedOut: r.timedOut,
    spawnError: r.spawnError, count: r.count,
    outputTail: r.output.trim().split('\n').slice(-10).join('\n'),
  };
}

module.exports = { loadSpec, stepProblems };

if (require.main === module) main();
