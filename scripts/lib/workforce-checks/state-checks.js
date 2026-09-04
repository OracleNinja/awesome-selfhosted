'use strict';

/**
 * state-checks.js — read-only preflight checks for the ECC state store.
 *
 * These checks inspect the sql.js-backed state database and its write lock
 * (see scripts/lib/state-store/write-lock.js) purely at the byte and
 * filesystem level. They never open the database and never acquire the
 * write lock, so they cannot block or be blocked by real work.
 *
 * Lock file naming, JSON shape ({token, pid, hostname, startedAt}), and the
 * liveness convention (process.kill(pid, 0); EPERM counts as alive; a lock
 * recorded from a different hostname is treated as unverifiable-but-alive)
 * mirror scripts/lib/state-store/write-lock.js exactly. That module is not
 * required from here to keep this check self-contained.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const SQLITE_HEADER = Buffer.from('SQLite format 3\0', 'ascii');
const EXPECTED_MODE = 0o600;

function makeResult(id, title, status, detail) {
  return { id, title, status, detail };
}

function safeCheck(id, title, fn) {
  try {
    return fn(id, title);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    return makeResult(id, title, 'FAIL', `unexpected error: ${message}`);
  }
}

function checkPresent(ctx) {
  return safeCheck('state-db-present', 'State database file present', (id, title) => {
    let stats;
    try {
      stats = fs.lstatSync(ctx.stateDbPath);
    } catch (error) {
      if (error.code === 'ENOENT') {
        return makeResult(id, title, 'FAIL', `does not exist: ${ctx.stateDbPath}`);
      }
      throw error;
    }
    if (stats.isSymbolicLink()) {
      return makeResult(id, title, 'FAIL', `is a symlink, not a regular file: ${ctx.stateDbPath}`);
    }
    if (stats.isDirectory()) {
      return makeResult(id, title, 'FAIL', `is a directory, not a regular file: ${ctx.stateDbPath}`);
    }
    if (!stats.isFile()) {
      return makeResult(id, title, 'FAIL', `is not a regular file: ${ctx.stateDbPath}`);
    }
    if (stats.size === 0) {
      return makeResult(id, title, 'FAIL', `file is empty: ${ctx.stateDbPath}`);
    }
    return makeResult(id, title, 'PASS', `regular file, ${stats.size} bytes`);
  });
}

function checkHeader(ctx) {
  return safeCheck('state-db-header', 'State database SQLite header', (id, title) => {
    let fd;
    try {
      fd = fs.openSync(ctx.stateDbPath, 'r');
    } catch (error) {
      return makeResult(id, title, 'FAIL', `cannot open file: ${error.message}`);
    }
    try {
      const buffer = Buffer.alloc(SQLITE_HEADER.length);
      const bytesRead = fs.readSync(fd, buffer, 0, buffer.length, 0);
      if (bytesRead < SQLITE_HEADER.length) {
        return makeResult(id, title, 'FAIL', `file too short for SQLite header (${bytesRead} bytes read)`);
      }
      if (!buffer.equals(SQLITE_HEADER)) {
        return makeResult(id, title, 'FAIL', `header mismatch: got ${JSON.stringify(buffer.toString('latin1'))}`);
      }
      return makeResult(id, title, 'PASS', 'SQLite magic header present');
    } finally {
      fs.closeSync(fd);
    }
  });
}

function checkPermissions(ctx) {
  return safeCheck('state-db-permissions', 'State database file permissions', (id, title) => {
    const stats = fs.statSync(ctx.stateDbPath);
    const mode = stats.mode & 0o777;
    const octal = mode.toString(8).padStart(3, '0');
    if (mode === EXPECTED_MODE) {
      return makeResult(id, title, 'PASS', `mode 0${octal}`);
    }
    return makeResult(id, title, 'WARN', `expected 0600, found 0${octal}`);
  });
}

function lockPathFor(dbPath) {
  return path.join(path.dirname(dbPath), `.${path.basename(dbPath)}.lock`);
}

// Mirrors write-lock.js's holderAlive(): missing/non-numeric pid or a
// mismatched hostname cannot be disproven, so both count as "alive".
function holderIsAlive(holder) {
  if (!holder || typeof holder.pid !== 'number') return true;
  if (holder.hostname && holder.hostname !== os.hostname()) return true;
  try {
    process.kill(holder.pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

function checkLock(ctx) {
  return safeCheck('state-store-lock', 'State store write lock', (id, title) => {
    const lockPath = lockPathFor(ctx.stateDbPath);
    let stats;
    try {
      stats = fs.lstatSync(lockPath);
    } catch (error) {
      if (error.code === 'ENOENT') {
        return makeResult(id, title, 'PASS', 'no lock file present');
      }
      throw error;
    }
    if (!stats.isFile()) {
      return makeResult(id, title, 'WARN', `lock path exists but is not a regular file: ${lockPath}`);
    }
    let holder;
    try {
      holder = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
    } catch (error) {
      return makeResult(id, title, 'FAIL', `lock file present but unreadable/malformed: ${error.message}`);
    }
    if (holderIsAlive(holder)) {
      const pidDetail = holder && typeof holder.pid === 'number' ? ` (pid ${holder.pid})` : '';
      return makeResult(id, title, 'WARN', `a writer holds the lock${pidDetail}`);
    }
    return makeResult(id, title, 'FAIL', `stale lock: recorded pid ${holder.pid} is not running`);
  });
}

function formatAge(ms) {
  const clamped = Math.max(0, ms);
  const seconds = Math.floor(clamped / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function checkFreshness(ctx) {
  return safeCheck('state-db-freshness', 'State database freshness', (id, title) => {
    const stats = fs.statSync(ctx.stateDbPath);
    const ageMs = Date.now() - stats.mtimeMs;
    return makeResult(id, title, 'PASS', `last modified ${formatAge(ageMs)} (${stats.mtime.toISOString()})`);
  });
}

module.exports = {
  id: 'state',
  title: 'State store checks',
  run(ctx) {
    return [
      checkPresent(ctx),
      checkHeader(ctx),
      checkPermissions(ctx),
      checkLock(ctx),
      checkFreshness(ctx),
    ];
  },
};
