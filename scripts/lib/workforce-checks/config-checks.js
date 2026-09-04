'use strict';

const fs = require('fs');
const path = require('path');

const STATUS = { PASS: 'PASS', WARN: 'WARN', FAIL: 'FAIL' };
const CONFINEMENT_SCRIPT = 'pre-worktree-confinement.js';
const REQUIRED_MATCHERS = ['Edit', 'Write', 'MultiEdit', 'NotebookEdit'];
const CONFINED_ROLE = 'ecc-worker';

function safeJoin(base, ...parts) {
  if (typeof base !== 'string' || base.length === 0) return null;
  return path.join(base, ...parts);
}

function loadSettings(ctx) {
  const settingsPath = safeJoin(ctx && ctx.claudeHome, 'settings.json');
  if (!settingsPath) {
    return { ok: false, error: 'ctx.claudeHome is missing or not a string', path: null };
  }
  try {
    if (!fs.existsSync(settingsPath)) {
      return { ok: false, error: `Missing file: ${settingsPath}`, path: settingsPath };
    }
    const data = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
    return { ok: true, data, path: settingsPath };
  } catch (err) {
    return { ok: false, error: `${settingsPath}: ${err.message}`, path: settingsPath };
  }
}

function loadSettingsSafely(ctx) {
  try {
    return loadSettings(ctx);
  } catch (err) {
    return { ok: false, error: `Unexpected error: ${err && err.message ? err.message : String(err)}`, path: null };
  }
}

function findConfinementHook(preToolUse) {
  if (!Array.isArray(preToolUse)) return null;
  for (const entry of preToolUse) {
    if (!entry || !Array.isArray(entry.hooks)) continue;
    for (const hook of entry.hooks) {
      if (hook && typeof hook.command === 'string' && hook.command.includes(CONFINEMENT_SCRIPT)) {
        return { entry, hook };
      }
    }
  }
  return null;
}

function matcherCoversAll(matcher) {
  if (typeof matcher !== 'string') return { ok: false, missing: REQUIRED_MATCHERS.slice() };
  const tokens = matcher.split(/[|,\s]+/).map((t) => t.trim()).filter(Boolean);
  const missing = REQUIRED_MATCHERS.filter((m) => !tokens.includes(m));
  return { ok: missing.length === 0, missing };
}

function extractScriptInvocation(command) {
  const parts = command.trim().split(/\s+/);
  const idx = parts.findIndex((p) => p.endsWith(CONFINEMENT_SCRIPT));
  if (idx === -1) return null;
  return { scriptPath: parts[idx], args: parts.slice(idx + 1) };
}

function checkSettingsParseable(settingsResult) {
  const id = 'settings-parseable';
  const title = 'settings.json exists and parses as JSON';
  if (!settingsResult.ok) return { id, title, status: STATUS.FAIL, detail: settingsResult.error };
  return { id, title, status: STATUS.PASS, detail: `Parsed OK: ${settingsResult.path}` };
}

function checkConfinementHookRegistered(settingsResult) {
  const id = 'confinement-hook-registered';
  const title = 'PreToolUse registers the worktree confinement hook';
  if (!settingsResult.ok) {
    return { id, title, status: STATUS.FAIL, detail: `Cannot evaluate: ${settingsResult.error}` };
  }
  const preToolUse = settingsResult.data && settingsResult.data.hooks && settingsResult.data.hooks.PreToolUse;
  const found = findConfinementHook(preToolUse);
  if (!found) {
    return { id, title, status: STATUS.FAIL, detail: `No PreToolUse hook references ${CONFINEMENT_SCRIPT}` };
  }
  const problems = [];
  const matcherCheck = matcherCoversAll(found.entry.matcher);
  if (!matcherCheck.ok) problems.push(`matcher missing: ${matcherCheck.missing.join(', ')}`);
  const invocation = extractScriptInvocation(found.hook.command);
  if (!invocation) {
    problems.push('could not parse hook command');
  } else if (invocation.args[0] !== CONFINED_ROLE) {
    problems.push(`confined-role argument is "${invocation.args[0] || ''}" not "${CONFINED_ROLE}"`);
  }
  if (problems.length > 0) return { id, title, status: STATUS.FAIL, detail: problems.join('; ') };
  return { id, title, status: STATUS.PASS, detail: `matcher="${found.entry.matcher}" role="${CONFINED_ROLE}"` };
}

function checkConfinementHookPresent(settingsResult) {
  const id = 'confinement-hook-present';
  const title = 'Confinement hook file exists and is readable';
  if (!settingsResult.ok) {
    return { id, title, status: STATUS.FAIL, detail: `Cannot evaluate: ${settingsResult.error}` };
  }
  const preToolUse = settingsResult.data && settingsResult.data.hooks && settingsResult.data.hooks.PreToolUse;
  const found = findConfinementHook(preToolUse);
  if (!found) return { id, title, status: STATUS.FAIL, detail: `No registration references ${CONFINEMENT_SCRIPT}` };
  const invocation = extractScriptInvocation(found.hook.command);
  if (!invocation) return { id, title, status: STATUS.FAIL, detail: 'Could not parse hook file path from command' };
  try {
    fs.accessSync(invocation.scriptPath, fs.constants.R_OK);
    return { id, title, status: STATUS.PASS, detail: `Readable: ${invocation.scriptPath}` };
  } catch (err) {
    return { id, title, status: STATUS.FAIL, detail: `Not present/readable: ${invocation.scriptPath} (${err.message})` };
  }
}

function parseFrontmatterTools(content) {
  const lines = content.split(/\r?\n/);
  if (lines[0].trim() !== '---') return null;
  let end = -1;
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i].trim() === '---') {
      end = i;
      break;
    }
  }
  if (end === -1) return null;
  const toolsLine = lines.slice(1, end).find((l) => /^tools\s*:/.test(l.trim()));
  if (!toolsLine) return null;
  const value = toolsLine.slice(toolsLine.indexOf(':') + 1).trim();
  return value.split(',').map((t) => t.trim()).filter(Boolean);
}

function checkWorkerGrantsNoBash(ctx) {
  const id = 'worker-grants-no-bash';
  const title = 'ecc-worker.md frontmatter does not grant Bash';
  const agentPath = safeJoin(ctx && ctx.claudeHome, 'agents', 'ecc-worker.md');
  if (!agentPath) return { id, title, status: STATUS.FAIL, detail: 'ctx.claudeHome is missing or not a string' };
  try {
    if (!fs.existsSync(agentPath)) return { id, title, status: STATUS.FAIL, detail: `Missing file: ${agentPath}` };
    const tools = parseFrontmatterTools(fs.readFileSync(agentPath, 'utf8'));
    if (!tools) return { id, title, status: STATUS.FAIL, detail: `Could not find "tools:" in frontmatter of ${agentPath}` };
    if (tools.includes('Bash')) return { id, title, status: STATUS.FAIL, detail: `Bash present in tools list: ${tools.join(', ')}` };
    return { id, title, status: STATUS.PASS, detail: `tools: ${tools.join(', ')}` };
  } catch (err) {
    return { id, title, status: STATUS.FAIL, detail: `Error reading ${agentPath}: ${err.message}` };
  }
}

function checkGateToolsPresent(ctx) {
  const id = 'gate-tools-present';
  const title = 'Gate CLI scripts exist';
  const names = ['ecc-integrate.js', 'ecc-worker-base.js'];
  const targets = names.map((n) => safeJoin(ctx && ctx.claudeHome, 'scripts', n));
  if (targets.some((p) => !p)) {
    return { id, title, status: STATUS.FAIL, detail: 'ctx.claudeHome is missing or not a string' };
  }
  const missing = targets.filter((p) => {
    try {
      return !fs.existsSync(p);
    } catch (err) {
      return true;
    }
  });
  if (missing.length > 0) return { id, title, status: STATUS.FAIL, detail: `Missing: ${missing.join(', ')}` };
  return { id, title, status: STATUS.PASS, detail: `Present: ${targets.join(', ')}` };
}

function safeRun(fn, id, title) {
  try {
    return fn();
  } catch (err) {
    return { id, title, status: STATUS.FAIL, detail: `Unexpected error: ${err && err.message ? err.message : String(err)}` };
  }
}

function run(ctx) {
  const safeCtx = ctx || {};
  const settingsResult = loadSettingsSafely(safeCtx);

  return [
    safeRun(() => checkSettingsParseable(settingsResult), 'settings-parseable', 'settings.json exists and parses as JSON'),
    safeRun(() => checkConfinementHookRegistered(settingsResult), 'confinement-hook-registered', 'PreToolUse registers the confinement hook'),
    safeRun(() => checkConfinementHookPresent(settingsResult), 'confinement-hook-present', 'Confinement hook file exists and is readable'),
    safeRun(() => checkWorkerGrantsNoBash(safeCtx), 'worker-grants-no-bash', 'ecc-worker.md frontmatter does not grant Bash'),
    safeRun(() => checkGateToolsPresent(safeCtx), 'gate-tools-present', 'Gate CLI scripts exist'),
  ];
}

module.exports = {
  id: 'config',
  title: 'Harness and agent configuration checks',
  run,
};
