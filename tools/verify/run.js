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
const path = require('path');

const VERIFY_DIR = __dirname;
const REPO_ROOT = path.resolve(VERIFY_DIR, '..', '..');
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
      if (spec[key] !== undefined && !Array.isArray(spec[key])) problems.push(`${key} is not an array`);
    }
  }
  if (problems.length) return { error: `${path.basename(file)}: ${problems.join('; ')}` };
  return { spec };
}

function runStep(cwd, step, label) {
  const timeout = Number(step.timeoutMs) || DEFAULT_TIMEOUT_MS;
  const started = Date.now();
  process.stdout.write(`    ${label} $ ${step.command} ${(step.args || []).join(' ')}\n`);
  const r = spawnSync(step.command, step.args || [], {
    cwd, timeout, encoding: 'utf8', env: process.env, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const durationMs = Date.now() - started;
  const output = `${r.stdout || ''}${r.stderr || ''}`;
  const timedOut = r.error && r.error.code === 'ETIMEDOUT';
  return {
    name: step.name, command: step.command, args: step.args || [],
    exitCode: r.status, durationMs, timedOut,
    spawnError: r.error && !timedOut ? r.error.message : null,
    output,
  };
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
      console.log(`  ${ok ? 'PASS' : 'FAIL'}  install  (${(r.durationMs / 1000).toFixed(1)}s, exit ${r.exitCode})`);
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
      entry.steps.push({ name: 'install', status: 'SKIP', detail: '--skip-install was passed' });
      if (entry.status === 'PASS') entry.status = 'WARN';
      console.log('  SKIP  install — --skip-install was passed; results below assume an existing environment');
    }

    // ---- required --------------------------------------------------------
    const required = spec.verify || [];
    if (required.length === 0) {
      entry.steps.push({ name: 'required verification', status: 'WARN', detail: 'this spec declares no required verification' });
      if (entry.status === 'PASS') entry.status = 'WARN';
      console.log('  WARN  this spec declares no required verification');
    }
    for (const step of required) {
      const r = runStep(cwd, step, 'verify');
      const ok = r.exitCode === 0;
      entry.steps.push({ name: step.name, status: ok ? 'PASS' : 'FAIL', ...strip(r) });
      console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${step.name}  (${(r.durationMs / 1000).toFixed(1)}s, exit ${r.exitCode})`);
      if (!ok) {
        console.log(r.timedOut ? '      | TIMED OUT' : tail(r.output));
        entry.status = 'FAIL';
      }
    }

    // ---- optional --------------------------------------------------------
    for (const step of spec.optional || []) {
      if (!opts.withOptional) {
        entry.steps.push({ name: step.name, status: 'SKIP', detail: step.reason || 'optional; pass --with-optional to run' });
        console.log(`  SKIP  ${step.name} — ${step.reason || 'optional; pass --with-optional to run'}`);
        continue;
      }
      const r = runStep(cwd, step, 'optional');
      const ok = r.exitCode === 0;
      entry.steps.push({ name: step.name, status: ok ? 'PASS' : 'FAIL', optional: true, ...strip(r) });
      console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${step.name} (optional, requested)  (${(r.durationMs / 1000).toFixed(1)}s, exit ${r.exitCode})`);
      if (!ok) { console.log(r.timedOut ? '      | TIMED OUT' : tail(r.output)); entry.status = 'FAIL'; }
    }

    // ---- conditional -----------------------------------------------------
    for (const step of spec.conditional || []) {
      const envName = step.requiresEnv;
      if (!envName || !process.env[envName]) {
        entry.steps.push({ name: step.name, status: 'SKIP', detail: `requires ${envName || 'an unnamed environment variable'}; not set` });
        console.log(`  SKIP  ${step.name} — requires ${envName || 'an unnamed environment variable'}, which is not set`);
        continue;
      }
      const r = runStep(cwd, step, 'conditional');
      const ok = r.exitCode === 0;
      entry.steps.push({ name: step.name, status: ok ? 'PASS' : 'FAIL', conditional: true, ...strip(r) });
      console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${step.name} (conditional, ${envName} set)  (${(r.durationMs / 1000).toFixed(1)}s, exit ${r.exitCode})`);
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
  console.log(`\n  steps: ${counts.PASS} pass, ${counts.FAIL} fail, ${counts.SKIP} skip, ${counts.WARN} warn`);
  console.log('  (SKIP means not run — it is not a pass)');

  if (opts.json) console.log(`\n${JSON.stringify(report, null, 2)}`);

  const failed = report.projects.some(p => p.status === 'FAIL') || broken.length > 0 || unknown.length > 0;
  process.exitCode = failed ? 1 : 0;
}

/** Keep structured output small; full text is streamed above when it matters. */
function strip(r) {
  return {
    command: r.command, args: r.args, exitCode: r.exitCode,
    durationMs: r.durationMs, timedOut: r.timedOut, spawnError: r.spawnError,
    outputTail: r.output.trim().split('\n').slice(-10).join('\n'),
  };
}

if (require.main === module) main();
