'use strict';

/**
 * git-checks.js — read-only git/target preflight checks for the ECC
 * Workforce Preflight Doctor (Phase 2G, Worker A).
 *
 * Interface contract (fixed by the Lead, shared across all workforce-checks
 * modules): module.exports = { id, title, run(ctx) }. run() must never throw;
 * every check is wrapped individually so one failure cannot abort the rest.
 *
 * Lock/liveness convention matches /root/.claude/scripts/lib/exclusive-lock.js
 * (readHolder/holderAlive): a lockfile is JSON {token, pid, hostname,
 * purpose, startedAt}; liveness is process.kill(pid, 0), EPERM counts as
 * alive, and a hostname that does not match the current host is treated as
 * unknown-but-alive (never displace a holder we cannot disprove).
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFileSync } = require('child_process');

function result(id, title, status, detail) {
  return { id, title, status, detail };
}

function repoRootValid(repoRoot) {
  return typeof repoRoot === 'string' && repoRoot.length > 0;
}

function runGit(args, cwd) {
  try {
    const stdout = execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    return { ok: true, stdout };
  } catch (error) {
    const stderr = (error && error.stderr && String(error.stderr)) || (error && error.message) || 'unknown error';
    return { ok: false, stdout: '', stderr: String(stderr) };
  }
}

/** Porcelain `git worktree list` -> [{path, head, branch, detached}] | null. */
function listWorktrees(repoRoot) {
  const r = runGit(['worktree', 'list', '--porcelain'], repoRoot);
  if (!r.ok) return null;
  const worktrees = [];
  let current = null;
  for (const rawLine of r.stdout.split('\n')) {
    const line = rawLine.trim();
    if (line.startsWith('worktree ')) {
      if (current) worktrees.push(current);
      current = { path: line.slice('worktree '.length).trim(), head: null, branch: null, detached: false };
    } else if (current && line.startsWith('HEAD ')) {
      current.head = line.slice('HEAD '.length).trim();
    } else if (current && line.startsWith('branch ')) {
      current.branch = line.slice('branch '.length).trim();
    } else if (current && line === 'detached') {
      current.detached = true;
    }
  }
  if (current) worktrees.push(current);
  return worktrees;
}

function checkTargetClean(repoRoot) {
  const id = 'target-clean';
  const title = 'Working tree is clean';
  if (!repoRootValid(repoRoot)) return result(id, title, 'FAIL', 'ctx.repoRoot is missing or not a string');
  if (!fs.existsSync(repoRoot)) return result(id, title, 'FAIL', `repoRoot does not exist: ${repoRoot}`);
  const r = runGit(['status', '--porcelain', '--untracked-files=all'], repoRoot);
  if (!r.ok) return result(id, title, 'FAIL', `git status failed: ${r.stderr}`);
  const lines = r.stdout.split('\n').map(l => l.trimEnd()).filter(Boolean);
  if (lines.length === 0) return result(id, title, 'PASS', 'working tree is clean');
  const preview = lines.slice(0, 10).join('; ');
  const more = lines.length > 10 ? ` (+${lines.length - 10} more)` : '';
  return result(id, title, 'FAIL', `working tree dirty: ${preview}${more}`);
}

function checkIntegrationLock(repoRoot) {
  const id = 'integration-lock';
  const title = 'Integration lock state';
  if (!repoRootValid(repoRoot)) return result(id, title, 'FAIL', 'ctx.repoRoot is missing or not a string');
  if (!fs.existsSync(repoRoot)) return result(id, title, 'FAIL', `repoRoot does not exist: ${repoRoot}`);
  const lockPath = path.join(repoRoot, '.git', 'ecc-integrate.lock');
  if (!fs.existsSync(lockPath)) return result(id, title, 'PASS', 'no integration lock present');
  let holder = null;
  try {
    holder = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
  } catch (parseError) {
    return result(id, title, 'FAIL', `lock file present but unreadable/corrupt: ${parseError.message}`);
  }
  const purpose = (holder && holder.purpose) || 'unknown';
  const startedAt = (holder && holder.startedAt) || 'unknown';
  const pid = holder && holder.pid;
  const differentHost = Boolean(holder && holder.hostname && holder.hostname !== os.hostname());
  let alive = true;
  if (typeof pid === 'number' && !differentHost) {
    try {
      process.kill(pid, 0);
      alive = true;
    } catch (killError) {
      alive = Boolean(killError && killError.code === 'EPERM');
    }
  }
  if (alive) return result(id, title, 'WARN', `an integration is in progress (pid=${pid}, purpose=${purpose}, startedAt=${startedAt})`);
  return result(id, title, 'FAIL', `stale lock: recorded pid ${pid} is not running (purpose=${purpose}, startedAt=${startedAt})`);
}

function checkCherryPickInProgress(repoRoot) {
  const id = 'cherry-pick-in-progress';
  const title = 'No cherry-pick in progress';
  if (!repoRootValid(repoRoot)) return result(id, title, 'FAIL', 'ctx.repoRoot is missing or not a string');
  if (!fs.existsSync(repoRoot)) return result(id, title, 'FAIL', `repoRoot does not exist: ${repoRoot}`);
  const cpPath = path.join(repoRoot, '.git', 'CHERRY_PICK_HEAD');
  if (!fs.existsSync(cpPath)) return result(id, title, 'PASS', 'no cherry-pick in progress');
  let sha = '';
  try { sha = fs.readFileSync(cpPath, 'utf8').trim(); } catch (_error) { /* best-effort detail only */ }
  return result(id, title, 'FAIL', `cherry-pick in progress${sha ? ` (CHERRY_PICK_HEAD=${sha})` : ''}`);
}

// Deliberately always PASS: this check is informational inventory, not a
// gate. On enumeration trouble (missing repoRoot, git failure) it still
// reports PASS with the problem folded into detail rather than failing.
function checkWorktreeInventory(repoRoot, cache) {
  const id = 'worktree-inventory';
  const title = 'Registered worktree inventory';
  if (!repoRootValid(repoRoot)) return result(id, title, 'PASS', 'ctx.repoRoot is missing or not a string; 0 worktrees reported');
  if (!fs.existsSync(repoRoot)) return result(id, title, 'PASS', `repoRoot does not exist: ${repoRoot}; 0 worktrees reported`);
  if (cache.worktrees === undefined) cache.worktrees = listWorktrees(repoRoot);
  const worktrees = cache.worktrees;
  if (worktrees === null) return result(id, title, 'PASS', 'could not enumerate worktrees (git worktree list failed); 0 worktrees reported');
  const paths = worktrees.map(w => w.path).join(', ') || '(none)';
  return result(id, title, 'PASS', `${worktrees.length} worktree(s) registered: ${paths}`);
}

function checkWorkerBaseStaleness(repoRoot, cache) {
  const id = 'worker-base-staleness';
  const title = 'Worker worktree base staleness vs main checkout';
  if (!repoRootValid(repoRoot)) return result(id, title, 'FAIL', 'ctx.repoRoot is missing or not a string');
  if (!fs.existsSync(repoRoot)) return result(id, title, 'FAIL', `repoRoot does not exist: ${repoRoot}`);
  if (cache.worktrees === undefined) cache.worktrees = listWorktrees(repoRoot);
  const worktrees = cache.worktrees;
  if (worktrees === null || worktrees.length === 0) {
    return result(id, title, 'FAIL', 'could not enumerate worktrees to compare against main checkout');
  }
  const resolvedRoot = path.resolve(repoRoot);
  const main = worktrees.find(w => path.resolve(w.path) === resolvedRoot) || worktrees[0];
  if (!main || !main.head) return result(id, title, 'FAIL', 'could not determine main checkout HEAD');
  const others = worktrees.filter(w => path.resolve(w.path) !== path.resolve(main.path));
  if (others.length === 0) return result(id, title, 'PASS', 'no other worktrees registered');

  const behind = [];
  const errors = [];
  for (const w of others) {
    if (!w.head) { errors.push(`${w.path} (no HEAD reported)`); continue; }
    if (w.head === main.head) continue;
    const r = runGit(['rev-list', '--count', `${w.head}..${main.head}`], repoRoot);
    if (!r.ok) { errors.push(`${w.path} (could not compute divergence: ${r.stderr})`); continue; }
    const count = parseInt(r.stdout.trim(), 10);
    if (Number.isFinite(count) && count > 0) behind.push(`${w.path} is ${count} commit(s) behind`);
  }

  if (behind.length === 0 && errors.length === 0) {
    return result(id, title, 'PASS', `all ${others.length} worker worktree(s) current with main checkout HEAD ${main.head}`);
  }
  if (behind.length === 0) {
    return result(id, title, 'WARN', `could not fully verify staleness: ${errors.join('; ')}`);
  }
  const suffix = errors.length ? `; also could not verify: ${errors.join('; ')}` : '';
  return result(id, title, 'WARN', `${behind.join('; ')}${suffix}`);
}

function safeCheck(fn, id, title) {
  try {
    return fn();
  } catch (error) {
    return result(id, title, 'FAIL', `unexpected error: ${error && error.message ? error.message : String(error)}`);
  }
}

function run(ctx) {
  const repoRoot = ctx && ctx.repoRoot;
  const cache = {};
  return [
    safeCheck(() => checkTargetClean(repoRoot), 'target-clean', 'Working tree is clean'),
    safeCheck(() => checkIntegrationLock(repoRoot), 'integration-lock', 'Integration lock state'),
    safeCheck(() => checkCherryPickInProgress(repoRoot), 'cherry-pick-in-progress', 'No cherry-pick in progress'),
    safeCheck(() => checkWorktreeInventory(repoRoot, cache), 'worktree-inventory', 'Registered worktree inventory'),
    safeCheck(() => checkWorkerBaseStaleness(repoRoot, cache), 'worker-base-staleness', 'Worker worktree base staleness vs main checkout'),
  ];
}

module.exports = {
  id: 'git',
  title: 'Git and target checks',
  run,
};
