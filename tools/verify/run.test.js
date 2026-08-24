'use strict';

/**
 * run.test.js — regression suite for the spec validation in run.js.
 * Run: node tools/verify/run.test.js
 * Exits 0 if all assertions pass, non-zero otherwise.
 *
 * These tests exist because of a defect found in Phase 2J. run.js documented
 * that "a malformed spec is a reportable failure, never a crash", and its
 * validation checked the spec object but never descended into the steps. A
 * step missing `command` therefore crashed the runner with a raw
 * ERR_INVALID_ARG_TYPE stack from spawnSync, and a step missing `name`
 * printed "PASS undefined" — both while validation reported the spec as
 * well-formed. Every case below is one that previously escaped the check.
 *
 * Fixtures are written under os.tmpdir() and removed in the finally block,
 * which opens before the first fixture is created so nothing leaks on a
 * mid-construction throw.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const { loadSpec, stepProblems } = require('./run.js');

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

/** loadSpec takes a path and require()s it, so fixtures must be real files. */
function writeSpec(dir, name, source) {
  const file = path.join(dir, name);
  fs.writeFileSync(file, source, 'utf8');
  return file;
}

const WELL_FORMED = {
  id: 'demo',
  dir: 'demo',
  install: { command: 'npm', args: ['ci'] },
  verify: [{ name: 'test', command: 'npm', args: ['test'] }],
};

function specSource(overrides) {
  return `module.exports = ${JSON.stringify({ ...WELL_FORMED, ...overrides })};`;
}

let tmp;
try {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'verify-run-test-'));

  // --- baseline: a well-formed spec still loads -------------------------
  {
    const r = loadSpec(writeSpec(tmp, 'ok.js', specSource({})));
    check('well-formed spec loads', !r.error, r.error);
    check('well-formed spec returns the spec', r.spec && r.spec.id === 'demo');
  }

  // --- the two defects this suite exists for ----------------------------
  {
    const r = loadSpec(writeSpec(tmp, 'nocommand.js',
      specSource({ verify: [{ name: 'test' }] })));
    check('step missing command is rejected', Boolean(r.error), 'expected an error');
    check('step missing command names the field',
      r.error && r.error.includes('verify[0] is missing command'), r.error);
  }
  {
    const r = loadSpec(writeSpec(tmp, 'noname.js',
      specSource({ verify: [{ command: 'npm', args: ['test'] }] })));
    check('step missing name is rejected', Boolean(r.error), 'expected an error');
    check('step missing name names the field',
      r.error && r.error.includes('verify[0] is missing name'), r.error);
  }

  // --- the same rules apply to every step list, not just verify ---------
  for (const key of ['optional', 'conditional']) {
    const r = loadSpec(writeSpec(tmp, `${key}-bad.js`,
      specSource({ [key]: [{ name: 'x' }] })));
    check(`${key} step missing command is rejected`,
      r.error && r.error.includes(`${key}[0] is missing command`), r.error);
  }

  // --- index is reported so a long list can be navigated ----------------
  {
    const r = loadSpec(writeSpec(tmp, 'second.js', specSource({
      verify: [{ name: 'a', command: 'true' }, { name: 'b' }],
    })));
    check('the offending step index is reported',
      r.error && r.error.includes('verify[1] is missing command'), r.error);
  }

  // --- args must be a string array; spawnSync rejects anything else -----
  {
    const r = loadSpec(writeSpec(tmp, 'argsobj.js',
      specSource({ verify: [{ name: 'test', command: 'npm', args: 'test' }] })));
    check('non-array args is rejected',
      r.error && r.error.includes('args is not an array'), r.error);
  }
  {
    const r = loadSpec(writeSpec(tmp, 'argsnum.js',
      specSource({ verify: [{ name: 'test', command: 'npm', args: [1] }] })));
    check('non-string arg is rejected',
      r.error && r.error.includes('args contains a non-string'), r.error);
  }

  // --- a non-object step must not throw on property access --------------
  {
    const r = loadSpec(writeSpec(tmp, 'nullstep.js',
      specSource({ verify: [null] })));
    check('null step is rejected, not dereferenced',
      r.error && r.error.includes('verify[0] is not an object'), r.error);
  }

  // --- install is named by the caller: command required, name not -------
  {
    const r = loadSpec(writeSpec(tmp, 'installok.js',
      specSource({ install: { command: 'npm', args: ['ci'] } })));
    check('install without a name is accepted', !r.error, r.error);
  }
  {
    const r = loadSpec(writeSpec(tmp, 'installbad.js',
      specSource({ install: { args: ['ci'] } })));
    check('install without a command is rejected',
      r.error && r.error.includes('install is missing command'), r.error);
  }

  // --- pre-existing spec-level checks must still hold -------------------
  {
    const r = loadSpec(writeSpec(tmp, 'noid.js', 'module.exports = { dir: "d" };'));
    check('spec missing id is still rejected',
      r.error && r.error.includes('missing id'), r.error);
  }
  {
    const r = loadSpec(writeSpec(tmp, 'notarray.js',
      specSource({ verify: 'nope' })));
    check('non-array verify is still rejected',
      r.error && r.error.includes('verify is not an array'), r.error);
    check('non-array verify does not also report step problems',
      r.error && !r.error.includes('verify[0]'), r.error);
  }

  // --- every problem is reported at once, not just the first ------------
  {
    const r = loadSpec(writeSpec(tmp, 'many.js',
      specSource({ verify: [{}, {}] })));
    const count = ((r.error || '').match(/verify\[/g) || []).length;
    check('all offending steps are reported together', count === 4,
      `expected 4 step problems, got ${count}: ${r.error}`);
  }

  // --- stepProblems is pure: a valid step yields nothing ----------------
  {
    const problems = stepProblems({ name: 'a', command: 'true', args: [] }, 'verify', 0);
    check('a valid step produces no problems', problems.length === 0, problems.join('; '));
  }
} finally {
  if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(`${passed} passed, ${failed} failed`);
process.exitCode = failed === 0 ? 0 : 1;
