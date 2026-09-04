#!/usr/bin/env node
/**
 * ecc-workforce-doctor.js — preflight for a multi-worker cycle.
 *
 * Composes the independent check modules under lib/workforce-checks/ and
 * reports whether the workforce is in a state where it is safe to start:
 * the target is clean, no lock is stranded, the confinement hook is actually
 * registered, workers grant no Bash, worker worktrees are current, and the
 * state store is intact.
 *
 * Every check is read-only. Nothing here opens the state database — doing so
 * would take its write lock, which a preflight must never hold.
 *
 * Exit codes: 0 all PASS, 1 at least one WARN, 2 at least one FAIL.
 *
 * Usage: ecc-workforce-doctor.js [--json] [--repo-root <path>] [--claude-home <path>]
 */

'use strict';

const path = require('path');

const MODULES = ['git-checks', 'config-checks', 'state-checks'];

function parseArgs(argv) {
  const out = { json: false };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === '--json') out.json = true;
    else if (argv[i] === '--repo-root') out.repoRoot = argv[++i];
    else if (argv[i] === '--claude-home') out.claudeHome = argv[++i];
    else if (argv[i] === '--ecc-root') out.eccRoot = argv[++i];
    else if (argv[i] === '--state-db') out.stateDbPath = argv[++i];
    else throw new Error(`Unknown option: ${argv[i]}`);
  }
  return out;
}

function main() {
  let opts;
  try { opts = parseArgs(process.argv); }
  catch (e) { console.error(e.message); process.exit(2); }

  const claudeHome = opts.claudeHome || path.join(require('os').homedir(), '.claude');
  const ctx = {
    repoRoot: opts.repoRoot || path.resolve(__dirname, '..'),
    claudeHome,
    eccRoot: opts.eccRoot || '/home/user/ECC',
    stateDbPath: opts.stateDbPath || path.join(claudeHome, 'ecc', 'state.db'),
  };

  const sections = [];
  for (const name of MODULES) {
    const modPath = path.join(__dirname, 'lib', 'workforce-checks', name);
    let mod;
    try {
      mod = require(modPath);
    } catch (error) {
      sections.push({ id: name, title: name, results: [{
        id: `${name}-load`, title: 'module loads', status: 'FAIL',
        detail: `could not load ${modPath}: ${error.message}`,
      }] });
      continue;
    }
    let results;
    try {
      results = mod.run(ctx);
    } catch (error) {
      // A module is contractually required not to throw; if one does, that is
      // itself a finding rather than a reason to abort the whole preflight.
      results = [{ id: `${mod.id}-threw`, title: 'run() did not throw', status: 'FAIL',
                   detail: `module threw: ${error.message}` }];
    }
    sections.push({ id: mod.id || name, title: mod.title || name, results: results || [] });
  }

  const all = sections.flatMap(s => s.results);
  const counts = { PASS: 0, WARN: 0, FAIL: 0 };
  for (const r of all) counts[r.status] = (counts[r.status] || 0) + 1;
  const verdict = counts.FAIL ? 'NOT READY' : counts.WARN ? 'READY WITH WARNINGS' : 'READY';

  if (opts.json) {
    console.log(JSON.stringify({ verdict, counts, ctx, sections }, null, 2));
  } else {
    console.log(`\nECC workforce preflight — ${ctx.repoRoot}\n`);
    for (const s of sections) {
      console.log(`  ${s.title}`);
      for (const r of s.results) console.log(`    ${r.status.padEnd(4)}  ${r.id.padEnd(26)} ${r.detail}`);
      console.log('');
    }
    console.log(`  ${verdict} — ${counts.PASS} pass, ${counts.WARN} warn, ${counts.FAIL} fail\n`);
  }

  process.exit(counts.FAIL ? 2 : counts.WARN ? 1 : 0);
}

if (require.main === module) main();
module.exports = { MODULES };
