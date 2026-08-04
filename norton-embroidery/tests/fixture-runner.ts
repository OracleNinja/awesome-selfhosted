/**
 * Shared fixture runner.
 *
 * Kept out of the test file so the golden-recording script can import it
 * without pulling in vitest's `describe`. Both paths therefore run the exact
 * same code, which is the point: the golden values describe the real pipeline.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { analyze, createProject, digitizeAndGenerate } from '../src/app/pipeline';
import { exportPes } from '../src/app/export-pes';
import { BROTHER_SE700 } from '../src/domain/machine';
import { buildStitchSequence } from '../src/domain/stitch-sequence';
import { mmToUnits, unitsToMm } from '../src/domain/units';
import { fromPngBuffer } from './fixtures';

const here = dirname(fileURLToPath(import.meta.url));
export const fixturesDir = join(here, '..', 'fixtures');
export const goldenPath = join(fixturesDir, 'golden.json');

export interface CatalogEntry {
  id: string;
  name: string;
  artwork: string;
  widthMm: number;
  heightMm: number;
  hoopId: string;
  colorCount: number;
  proves: string;
}

export const catalog: { designs: CatalogEntry[] } = JSON.parse(
  readFileSync(join(fixturesDir, 'catalog.json'), 'utf8'),
);

export interface FixtureMetrics {
  stitchCount: number;
  colorBlocks: number;
  threadCones: number;
  colorChanges: number;
  trims: number;
  jumps: number;
  widthMm: number;
  heightMm: number;
  objects: number;
  fillObjects: number;
  satinObjects: number;
  runningObjects: number;
  errors: number;
  warnings: number;
  pesBytes: number;
  sequenceId: string;
}

const machine = BROTHER_SE700;

/** Run one catalogue entry through the real pipeline. */
export function runFixture(entry: CatalogEntry): {
  metrics: FixtureMetrics;
  bytes: Uint8Array;
  design: ReturnType<typeof digitizeAndGenerate>['design'];
} {
  const png = readFileSync(join(fixturesDir, entry.artwork));
  const image = fromPngBuffer(png);

  const { design: project } = createProject({
    name: entry.name,
    customer: 'Fixture',
    machineId: machine.id,
    hoopId: entry.hoopId,
    width: mmToUnits(entry.widthMm),
    height: mmToUnits(entry.heightMm),
  });

  const analysis = analyze(image, project.canvas, { colorCount: entry.colorCount });
  const { design } = digitizeAndGenerate(project, analysis, machine);
  const sequence = buildStitchSequence(design);
  const result = exportPes(design, machine, { acknowledgeWarnings: true });

  if (!result.ok || !result.bytes) {
    throw new Error(`${entry.id}: export failed — ${result.blockedReason ?? 'unknown reason'}`);
  }

  const metrics: FixtureMetrics = {
    stitchCount: sequence.stats.stitchCount,
    colorBlocks: sequence.blocks.length,
    threadCones: sequence.distinctThreads.length,
    colorChanges: sequence.stats.colorChangeCount,
    trims: sequence.stats.trimCount,
    jumps: sequence.stats.jumpCount,
    widthMm: Number(unitsToMm(sequence.stats.width).toFixed(1)),
    heightMm: Number(unitsToMm(sequence.stats.height).toFixed(1)),
    objects: design.objects.length,
    fillObjects: design.objects.filter((o) => o.type === 'fill').length,
    satinObjects: design.objects.filter((o) => o.type === 'satin').length,
    runningObjects: design.objects.filter((o) => o.type === 'running').length,
    errors: design.validation?.errorCount ?? -1,
    warnings: design.validation?.warningCount ?? -1,
    pesBytes: result.bytes.length,
    sequenceId: result.sequenceId,
  };

  return { metrics, bytes: result.bytes, design };
}

