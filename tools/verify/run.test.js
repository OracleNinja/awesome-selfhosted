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

const { spawnSync } = require('child_process');

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

  // =====================================================================
  // EXECUTION BEHAVIOUR
  //
  // Everything above tests what the runner rejects. Everything below runs
  // the runner as a real child process against deterministic fixture specs
  // and asserts on what it actually did: which commands ran, how they were
  // graded, what the exit code was, and how many tests it could account
  // for. Nothing here reads run.js's source and infers behaviour from it,
  // and nothing here depends on a product being installed — every fixture
  // command is `node -e`, which is already required to run this suite.
  // =====================================================================

  const RUNNER = path.join(__dirname, 'run.js');

  /** Build a throwaway spec directory + repo root, run the real runner over
   *  it, and hand back the parsed JSON report alongside the raw output. */
  function runFixture(specs, { args = [], env = {}, helpers = {} } = {}) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'verify-fixture-'));
    const specDir = path.join(root, 'specs');
    fs.mkdirSync(specDir);
    for (const [name, content] of Object.entries(helpers)) {
      fs.writeFileSync(path.join(root, name), content, 'utf8');
    }
    for (const [name, spec] of Object.entries(specs)) {
      fs.mkdirSync(path.join(root, spec.dir), { recursive: true });
      // Fixtures that need a real script on disk refer to it as __ROOT__.
      const source = spec.source.split('__ROOT__').join(root.split('\\').join('\\\\'));
      fs.writeFileSync(path.join(specDir, `${name}.js`), `module.exports = ${source};`, 'utf8');
    }
    const r = spawnSync(process.execPath, [RUNNER, ...args, '--json'], {
      encoding: 'utf8',
      env: { ...process.env, VERIFY_SPEC_DIR: specDir, VERIFY_REPO_ROOT: root, ...env },
    });
    const stdout = r.stdout || '';
    let report = null;
    const start = stdout.lastIndexOf('\n{\n');
    if (start !== -1) {
      try { report = JSON.parse(stdout.slice(start)); } catch { /* leave null */ }
    }
    fs.rmSync(root, { recursive: true, force: true });
    return { exitCode: r.status, stdout, stderr: r.stderr || '', report };
  }

  const stepOf = (report, project, name) =>
    (report.projects.find((p) => p.id === project) || { steps: [] })
      .steps.find((st) => st.name === name);

  // A node -e program, quoted for use as a spec arg.
  const node = (body) => ({ command: process.execPath, args: ['-e', body] });
  const okStep = (name) => `{ name: '${name}', command: ${JSON.stringify(process.execPath)}, args: ['-e', 'process.exit(0)'] }`;

  // --- A. a valid spec executes successfully ----------------------------
  {
    const { exitCode, report } = runFixture({
      alpha: { dir: 'alpha', source: `{ id: 'alpha', dir: 'alpha', verify: [${okStep('unit')}] }` },
    });
    check('A: valid spec exits zero', exitCode === 0, `exit ${exitCode}`);
    check('A: valid spec is graded PASS', report && report.projects[0].status === 'PASS');
    check('A: its step is graded PASS', stepOf(report, 'alpha', 'unit').status === 'PASS');
  }

  // --- B. required failure produces FAIL and non-zero exit --------------
  {
    const { exitCode, report, stdout } = runFixture({
      beta: {
        dir: 'beta',
        source: `{ id: 'beta', dir: 'beta', verify: [{ name: 'unit', command: ${JSON.stringify(process.execPath)}, args: ['-e', 'console.error("boom-marker"); process.exit(3)'] }] }`,
      },
    });
    check('B: required failure exits non-zero', exitCode !== 0, `exit ${exitCode}`);
    check('B: project is graded FAIL', report && report.projects[0].status === 'FAIL');
    const st = stepOf(report, 'beta', 'unit');
    check('B: the failing step is graded FAIL', st.status === 'FAIL');
    check('B: the real exit code is preserved', st.exitCode === 3, `got ${st.exitCode}`);
    // N. failure diagnostics
    check('N: failure output reaches the log', stdout.includes('boom-marker'), 'stderr was swallowed');
    check('N: failure output is kept in the report', st.outputTail.includes('boom-marker'));
  }

  // --- C. optional step is SKIP unless requested ------------------------
  {
    const spec = {
      gamma: {
        dir: 'gamma',
        source: `{ id: 'gamma', dir: 'gamma', verify: [${okStep('unit')}], optional: [{ name: 'e2e', command: ${JSON.stringify(process.execPath)}, args: ['-e', 'process.exit(0)'], reason: 'needs a display' }] }`,
      },
    };
    const off = runFixture(spec);
    const skipped = stepOf(off.report, 'gamma', 'e2e');
    check('C: optional step skips by default', skipped.status === 'SKIP', skipped.status);
    check('C: the skip carries its reason', String(skipped.detail).includes('needs a display'));
    check('C: a skip does not fail the run', off.exitCode === 0);
    check('C: SKIP is not silently a PASS', off.report.projects[0].status === 'PASS' && skipped.status !== 'PASS');

    const on = runFixture(spec, { args: ['--with-optional'] });
    check('C: --with-optional actually runs it', stepOf(on.report, 'gamma', 'e2e').status === 'PASS');
  }

  // --- D/E. conditional depends on its environment variable -------------
  {
    const spec = {
      delta: {
        dir: 'delta',
        source: `{ id: 'delta', dir: 'delta', verify: [${okStep('unit')}], conditional: [{ name: 'db', requiresEnv: 'FIXTURE_DB_URL', command: ${JSON.stringify(process.execPath)}, args: ['-e', 'process.exit(0)'] }] }`,
      },
    };
    const without = runFixture(spec);
    const s1 = stepOf(without.report, 'delta', 'db');
    check('D: conditional skips when its variable is unset', s1.status === 'SKIP', s1.status);
    check('D: the skip names the variable', String(s1.detail).includes('FIXTURE_DB_URL'));

    const with_ = runFixture(spec, { env: { FIXTURE_DB_URL: 'postgres://fixture' } });
    check('E: conditional runs when its variable is set', stepOf(with_.report, 'delta', 'db').status === 'PASS');
  }

  // --- F. WARN is its own outcome, distinct from PASS -------------------
  {
    const { exitCode, report } = runFixture({
      epsilon: { dir: 'epsilon', source: `{ id: 'epsilon', dir: 'epsilon', verify: [] }` },
    });
    check('F: a spec with nothing required is WARN, not PASS', report.projects[0].status === 'WARN', report.projects[0].status);
    check('F: WARN does not fail the run', exitCode === 0, `exit ${exitCode}`);
    const warned = report.projects[0].steps.find((st) => st.status === 'WARN');
    check('F: the WARN is recorded as a step outcome', Boolean(warned));
  }
  {
    // --skip-install downgrades an otherwise-passing project to WARN.
    const { report } = runFixture({
      zeta: {
        dir: 'zeta',
        source: `{ id: 'zeta', dir: 'zeta', install: { command: ${JSON.stringify(process.execPath)}, args: ['-e', 'process.exit(0)'] }, verify: [${okStep('unit')}] }`,
      },
    }, { args: ['--skip-install'] });
    check('F: skipping install downgrades PASS to WARN', report.projects[0].status === 'WARN', report.projects[0].status);
    check('F: the install step is reported SKIP', stepOf(report, 'zeta', 'install').status === 'SKIP');
  }

  // --- G/H/I/J/K. malformed input fails closed, without crashing --------
  {
    const cases = [
      ['G: malformed spec', `{ dir: 'x' }`, 'missing id'],
      ['H: non-object step', `{ id: 'x', dir: 'x', verify: [null] }`, 'verify[0] is not an object'],
      ['I: step missing command', `{ id: 'x', dir: 'x', verify: [{ name: 'a' }] }`, 'verify[0] is missing command'],
      ['J: step missing name', `{ id: 'x', dir: 'x', verify: [{ command: 'true' }] }`, 'verify[0] is missing name'],
      ['K: invalid args', `{ id: 'x', dir: 'x', verify: [{ name: 'a', command: 'true', args: 'nope' }] }`, 'args is not an array'],
    ];
    for (const [label, source, expected] of cases) {
      const { exitCode, stdout, stderr } = runFixture({ bad: { dir: 'x', source } });
      check(`${label} exits non-zero`, exitCode !== 0, `exit ${exitCode}`);
      check(`${label} is reported, not thrown`, stdout.includes(expected), stdout.slice(-200));
      check(`${label} does not produce a stack trace`, !stderr.includes('    at '), stderr.slice(0, 200));
    }
  }

  // --- timeout ----------------------------------------------------------
  {
    const { exitCode, report, stdout } = runFixture({
      eta: {
        dir: 'eta',
        source: `{ id: 'eta', dir: 'eta', verify: [{ name: 'hangs', timeoutMs: 300, command: ${JSON.stringify(process.execPath)}, args: ['-e', 'setTimeout(() => {}, 60000)'] }] }`,
      },
    });
    const st = stepOf(report, 'eta', 'hangs');
    check('timeout: a hanging step is killed and graded FAIL', st.status === 'FAIL', st.status);
    check('timeout: it is marked as timed out', st.timedOut === true);
    check('timeout: the run exits non-zero', exitCode !== 0);
    check('timeout: the log says TIMED OUT', stdout.includes('TIMED OUT'));
  }

  // --- args are actually passed through ---------------------------------
  {
    const { report } = runFixture({
      theta: {
        dir: 'theta',
        source: `{ id: 'theta', dir: 'theta', verify: [{ name: 'argcheck', command: ${JSON.stringify(process.execPath)}, args: ['-e', 'process.exit(process.argv[1] === "sentinel" ? 0 : 9)', 'sentinel'] }] }`,
      },
    });
    check('args: declared arguments reach the command', stepOf(report, 'theta', 'argcheck').status === 'PASS',
      'the fixture exits 9 unless it receives its argument');
  }

  // --- L/M. execution evidence is present on a PASS ---------------------
  {
    const { stdout, report } = runFixture({
      iota: { dir: 'iota', source: `{ id: 'iota', dir: 'iota', verify: [${okStep('unit')}] }` },
    });
    const st = stepOf(report, 'iota', 'unit');
    check('M: PASS records the command that ran', st.command === process.execPath, st.command);
    check('M: PASS records the arguments', Array.isArray(st.args) && st.args[0] === '-e');
    check('M: PASS records the exit code', st.exitCode === 0);
    check('M: PASS records a duration', typeof st.durationMs === 'number' && st.durationMs >= 0);
    check('M: PASS records which tier the step belongs to', st.tier === 'required', st.tier);
    check('L: the printed line carries status, tier and exit', /PASS {2}unit {2}\[required\] {2}exit 0/.test(stdout), stdout.slice(0, 400));
    check('L: a step total is summarised', /steps: \d+ pass/.test(stdout));
  }

  // --- O. UNKNOWN count is explicit, and never zero ---------------------
  {
    const { stdout, report } = runFixture({
      kappa: { dir: 'kappa', source: `{ id: 'kappa', dir: 'kappa', verify: [${okStep('unit')}] }` },
    });
    const c = stepOf(report, 'kappa', 'unit').count;
    check('O: an uncountable step is marked not-known', c && c.known === false, JSON.stringify(c));
    check('O: it is not reported as zero tests', !('total' in c), JSON.stringify(c));
    check('O: it explains why it is unknown', typeof c.reason === 'string' && c.reason.length > 0);
    check('O: the log says UNKNOWN', stdout.includes('tests UNKNOWN'));
    check('O: UNKNOWN steps are counted separately in the summary',
      /report tests UNKNOWN — not counted, not assumed zero/.test(stdout), stdout.slice(-400));
    check('O: no tests are claimed to have run', report.totals.tests.ran === 0 && report.totals.tests.countedSteps === 0);
  }

  // --- an explicit "not a test runner" declaration reads as n/a ---------
  {
    const { stdout, report } = runFixture({
      lambda: {
        dir: 'lambda',
        source: `{ id: 'lambda', dir: 'lambda', verify: [{ name: 'typecheck', command: ${JSON.stringify(process.execPath)}, args: ['-e', 'process.exit(0)'], count: { strategy: 'none', reason: 'a typecheck runs no tests' } }] }`,
      },
    });
    const c = stepOf(report, 'lambda', 'typecheck').count;
    check('n/a: a declared no-count step is distinguished from UNKNOWN',
      c.known === false && c.notApplicable === true, JSON.stringify(c));
    check('n/a: the log shows n/a rather than UNKNOWN',
      stdout.includes('tests n/a') && !stdout.includes('tests UNKNOWN'));
  }

  // --- counts come from machine-readable artifacts ----------------------
  // The fixture writes the artifact the strategy asks for, so this exercises
  // the real plumbing: flag/env injection, artifact discovery, and parsing.
  const JUNIT_WRITER =
    'const f = /--junitxml=(\\S+)/.exec(process.env.PYTEST_ADDOPTS || "")[1];' +
    'require("fs").writeFileSync(f, `<testsuites><testsuite name="s" tests="${process.env.FIXTURE_TESTS}" skipped="${process.env.FIXTURE_SKIPPED}"></testsuite></testsuites>`);';
  // Written to disk rather than passed with -e: node parses `--reporter=...`
  // as one of its own options when it follows -e, but stops option parsing at
  // a script path. A real test runner has the same property.
  const VITEST_WRITER_FILE = [
    'const flag = process.argv.find((x) => x.startsWith("--outputFile="));',
    'if (!flag) { console.error("no --outputFile was passed"); process.exit(9); }',
    'require("fs").writeFileSync(flag.slice("--outputFile=".length), JSON.stringify({',
    '  numTotalTests: Number(process.env.FIXTURE_TESTS),',
    '  numPendingTests: Number(process.env.FIXTURE_SKIPPED),',
    '}));',
  ].join('\n');

  function countingSpec(id, strategy, body) {
    return {
      [id]: {
        dir: id,
        source: `{ id: '${id}', dir: '${id}', verify: [{ name: 'suite', command: ${JSON.stringify(process.execPath)}, args: ['-e', ${JSON.stringify(body)}], count: { strategy: '${strategy}' } }] }`,
      },
    };
  }

  {
    const { stdout, report } = runFixture(countingSpec('mu', 'junit-xml', JUNIT_WRITER),
      { env: { FIXTURE_TESTS: '12', FIXTURE_SKIPPED: '2' } });
    const c = stepOf(report, 'mu', 'suite').count;
    check('count: junit-xml yields a real total', c.known === true && c.total === 12, JSON.stringify(c));
    check('count: junit-xml separates skipped from executed', c.skipped === 2 && c.executed === 10, JSON.stringify(c));
    check('count: the log shows the breakdown', stdout.includes('tests 12 (10 ran, 2 skipped)'), stdout.slice(0, 500));
  }
  {
    const nuSpec = {
      nu: {
        dir: 'nu',
        source: `{ id: 'nu', dir: 'nu', verify: [{ name: 'suite', command: ${JSON.stringify(process.execPath)}, args: ['__ROOT__/vitest-writer.js'], count: { strategy: 'vitest-json' } }] }`,
      },
    };
    const { report } = runFixture(nuSpec, {
      env: { FIXTURE_TESTS: '40', FIXTURE_SKIPPED: '0' },
      helpers: { 'vitest-writer.js': VITEST_WRITER_FILE },
    });
    const c = stepOf(report, 'nu', 'suite').count;
    check('count: vitest-json yields a real total', c.known === true && c.total === 40, JSON.stringify(c));
    check('count: the runner extended the arguments to obtain it',
      stepOf(report, 'nu', 'suite').argsWereExtended === true);
  }
  {
    // A strategy whose artifact never appears must degrade to UNKNOWN, not 0.
    const { report } = runFixture({
      xi: {
        dir: 'xi',
        source: `{ id: 'xi', dir: 'xi', verify: [{ name: 'suite', command: ${JSON.stringify(process.execPath)}, args: ['-e', 'process.exit(0)'], count: { strategy: 'vitest-json' } }] }`,
      },
    });
    const c = stepOf(report, 'xi', 'suite').count;
    check('count: a missing artifact degrades to UNKNOWN', c.known === false && !('total' in c), JSON.stringify(c));
    check('count: and says the artifact was missing', /wrote no artifact/.test(c.reason), c.reason);
  }

  // --- P. a shrunken suite is visible to the consumer of the report -----
  // This is the false-confidence case: both runs are green, and the only
  // thing that distinguishes a healthy suite from a gutted one is the count.
  {
    const big = runFixture(countingSpec('omicron', 'junit-xml', JUNIT_WRITER),
      { env: { FIXTURE_TESTS: '87', FIXTURE_SKIPPED: '0' } });
    const small = runFixture(countingSpec('omicron', 'junit-xml', JUNIT_WRITER),
      { env: { FIXTURE_TESTS: '3', FIXTURE_SKIPPED: '0' } });

    check('P: both the full and the gutted suite report PASS',
      big.report.projects[0].status === 'PASS' && small.report.projects[0].status === 'PASS');
    check('P: both exit zero', big.exitCode === 0 && small.exitCode === 0);
    check('P: the reported totals differ',
      big.report.totals.tests.ran === 87 && small.report.totals.tests.ran === 3,
      `${big.report.totals.tests.ran} vs ${small.report.totals.tests.ran}`);
    check('P: a consumer can detect the regression from the report alone',
      small.report.totals.tests.ran < big.report.totals.tests.ran);
    check('P: and from the printed output alone',
      big.stdout.includes('tests: 87 ran') && small.stdout.includes('tests: 3 ran'));
  }

} finally {
  if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(`${passed} passed, ${failed} failed`);
process.exitCode = failed === 0 ? 0 : 1;
