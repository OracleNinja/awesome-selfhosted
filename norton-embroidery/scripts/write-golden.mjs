/**
 * Record the current pipeline behaviour as `fixtures/golden.json`.
 *
 *   npm run fixtures:golden
 *
 * These values are a record of what the engine does today, not a target. Run
 * this only when a behaviour change has been reviewed and is intended; a diff
 * in this file is the evidence that the digitizer changed, which is what ties a
 * physical stitch-out back to a known engine state.
 */

import { writeFileSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const fixturesDir = join(root, 'fixtures');

// Load the TypeScript pipeline through Vite so this script does not need a
// separate build step.
const server = await createServer({
  root,
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
});

try {
  const { runFixture } = await server.ssrLoadModule('/tests/fixture-runner.ts');
  const catalog = JSON.parse(readFileSync(join(fixturesDir, 'catalog.json'), 'utf8'));

  const designs = {};
  for (const entry of catalog.designs) {
    const { metrics } = runFixture(entry);
    designs[entry.id] = metrics;
    console.log(
      `${entry.id.padEnd(24)} ${String(metrics.stitchCount).padStart(6)} stitches  ` +
        `${metrics.colorBlocks} block(s)  ${metrics.threadCones} cone(s)  ` +
        `${metrics.widthMm}x${metrics.heightMm} mm  seq ${metrics.sequenceId.slice(0, 8)}`,
    );
  }

  writeFileSync(
    join(fixturesDir, 'golden.json'),
    `${JSON.stringify(
      {
        note: 'Recorded behaviour of the digitizing engine for the controlled test designs. Regenerate deliberately with: npm run fixtures:golden',
        recordedAt: new Date().toISOString(),
        designs,
      },
      null,
      2,
    )}\n`,
  );
  console.log(`\nwrote ${join(fixturesDir, 'golden.json')}`);
} finally {
  await server.close();
}
