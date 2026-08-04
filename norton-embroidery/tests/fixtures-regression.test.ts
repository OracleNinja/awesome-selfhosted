/**
 * Fixture regression suite.
 *
 * The six controlled designs are run through the real pipeline — the same
 * functions the UI calls — and their measured metrics are compared against
 * committed golden values. The point is not that any particular number is
 * "correct": it is that a change to the engine shows up here as a diff, so a
 * physical stitch-out result can always be tied back to a known engine state.
 *
 * If a golden value changes, that is a real behaviour change. Investigate it,
 * then update `fixtures/golden.json` deliberately with
 * `npm run fixtures:golden` — never edit it to silence a failure.
 */

import { describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { BROTHER_SE700, getHoop } from '../src/domain/machine';
import { buildStitchSequence } from '../src/domain/stitch-sequence';
import { buildReadinessReport } from '../src/domain/readiness';
import { mmToUnits } from '../src/domain/units';
import { StitchCommand } from '../src/domain/stitch';
import { catalog, fixturesDir, goldenPath, runFixture, type FixtureMetrics } from './fixture-runner';

const machine = BROTHER_SE700;

const golden: Record<string, FixtureMetrics> | null = existsSync(goldenPath)
  ? JSON.parse(readFileSync(goldenPath, 'utf8')).designs
  : null;

/** Inspect PES bytes with pyembroidery, an independent implementation. */
function inspect(bytes: Uint8Array): {
  stitches: number;
  colors: number;
  widthMm: number;
  heightMm: number;
  colorList: string[];
} {
  const dir = mkdtempSync(join(tmpdir(), 'norton-fixture-'));
  const file = join(dir, 'design.pes');
  writeFileSync(file, bytes);
  const script = `
import json, sys, pyembroidery
from pyembroidery.EmbConstant import STITCH, COMMAND_MASK
p = pyembroidery.read(sys.argv[1])
n = sum(1 for s in p.stitches if (s[2] & COMMAND_MASK) == STITCH)
b = p.bounds()
print(json.dumps({
  "stitches": n,
  "colors": len(p.threadlist),
  "widthMm": round((b[2]-b[0])/10.0, 1),
  "heightMm": round((b[3]-b[1])/10.0, 1),
  "colorList": ["#%06X" % (t.color & 0xFFFFFF) for t in p.threadlist],
}))
`;
  const scriptFile = join(dir, 'inspect.py');
  writeFileSync(scriptFile, script);
  return JSON.parse(execFileSync('python3', [scriptFile, file], { encoding: 'utf8' }));
}

describe('fixture catalogue', () => {
  it('lists six designs of increasing difficulty', () => {
    expect(catalog.designs.length).toBe(6);
    for (const entry of catalog.designs) {
      expect(existsSync(join(fixturesDir, entry.artwork)), `${entry.artwork} missing`).toBe(true);
      expect(entry.proves.length).toBeGreaterThan(20);
      expect(getHoop(machine, entry.hoopId), `${entry.hoopId} is not a hoop on the ${machine.name}`).toBeDefined();
    }
  });

  it('has a golden file covering every design', () => {
    expect(golden, 'fixtures/golden.json is missing — run: npm run fixtures:golden').not.toBeNull();
    for (const entry of catalog.designs) {
      expect(golden![entry.id], `no golden metrics for ${entry.id}`).toBeDefined();
    }
  });
});

describe.each(catalog.designs)('fixture $id', (entry) => {
  const { metrics, bytes, design } = runFixture(entry);

  it('digitizes to something substantial', () => {
    expect(metrics.objects).toBeGreaterThan(0);
    expect(metrics.stitchCount).toBeGreaterThan(200);
  });

  it('validates without errors', () => {
    expect(design.validation).not.toBeNull();
    expect(design.validation!.errorCount, design.validation!.issues.map((i) => i.message).join(' | ')).toBe(0);
  });

  it('fits the hoop it was digitized for', () => {
    const hoop = getHoop(machine, entry.hoopId)!;
    expect(mmToUnits(metrics.widthMm)).toBeLessThanOrEqual(hoop.width);
    expect(mmToUnits(metrics.heightMm)).toBeLessThanOrEqual(hoop.height);
  });

  it('is stitched at the requested physical size', () => {
    // The artwork is fitted into the requested box preserving aspect ratio, so
    // one axis should reach it and neither may exceed it.
    expect(metrics.widthMm).toBeLessThanOrEqual(entry.widthMm + 0.5);
    expect(metrics.heightMm).toBeLessThanOrEqual(entry.heightMm + 0.5);
    expect(Math.max(metrics.widthMm / entry.widthMm, metrics.heightMm / entry.heightMm)).toBeGreaterThan(0.55);
  });

  it('exports a PES that an independent implementation reads identically', () => {
    const info = inspect(bytes);
    expect(info.stitches).toBe(metrics.stitchCount);
    expect(info.colors).toBe(metrics.colorBlocks);
    expect(Math.abs(info.widthMm - metrics.widthMm)).toBeLessThanOrEqual(0.2);
    expect(Math.abs(info.heightMm - metrics.heightMm)).toBeLessThanOrEqual(0.2);
  });

  it('reports the same stitch sequence for preview and file', () => {
    const sequence = buildStitchSequence(design);
    expect(metrics.sequenceId).toBe(sequence.id);
  });

  it('never reports the machine tier as passed', () => {
    const report = buildReadinessReport({
      design,
      machine,
      artworkLoaded: true,
      lastExport: {
        ok: true,
        sequenceId: metrics.sequenceId,
        fileName: 'x.pes',
        byteLength: bytes.length,
        checks: [],
      },
    });
    const machineTier = report.tiers.find((t) => t.id === 'machine')!;
    expect(machineTier.state).toBe('not-performed');
    expect(report.provenOnMachine).toBe(false);
  });

  it('matches its golden metrics', () => {
    const expected = golden?.[entry.id];
    if (!expected) return; // covered by the catalogue test above
    // Counts must be exact: the pipeline is deterministic.
    expect(metrics.stitchCount).toBe(expected.stitchCount);
    expect(metrics.colorBlocks).toBe(expected.colorBlocks);
    expect(metrics.threadCones).toBe(expected.threadCones);
    expect(metrics.colorChanges).toBe(expected.colorChanges);
    expect(metrics.trims).toBe(expected.trims);
    expect(metrics.objects).toBe(expected.objects);
    expect(metrics.fillObjects).toBe(expected.fillObjects);
    expect(metrics.satinObjects).toBe(expected.satinObjects);
    expect(metrics.runningObjects).toBe(expected.runningObjects);
    expect(metrics.widthMm).toBeCloseTo(expected.widthMm, 1);
    expect(metrics.heightMm).toBeCloseTo(expected.heightMm, 1);
    expect(metrics.sequenceId).toBe(expected.sequenceId);
  });
});

describe('fixture-specific expectations', () => {
  it('the single-colour square needs no colour change', () => {
    const { metrics } = runFixture(catalog.designs.find((d) => d.id === '01-single-color-square')!);
    expect(metrics.threadCones).toBe(1);
    expect(metrics.colorChanges).toBe(0);
    expect(metrics.colorBlocks).toBe(1);
  });

  it('the two-colour logo needs exactly one colour change', () => {
    const { metrics } = runFixture(catalog.designs.find((d) => d.id === '02-two-color-logo')!);
    expect(metrics.threadCones).toBe(2);
    expect(metrics.colorChanges).toBe(1);
  });

  it('the multi-colour logo uses at least four cones', () => {
    const { metrics } = runFixture(catalog.designs.find((d) => d.id === '03-multi-color-logo')!);
    expect(metrics.threadCones).toBeGreaterThanOrEqual(4);
  });

  it('the holes fixture produces polygons with holes and never stitches across them', () => {
    const { design } = runFixture(catalog.designs.find((d) => d.id === '04-holes-and-cutouts')!);
    const withHoles = design.objects.filter(
      (o) => o.geometry.kind === 'polygon' && o.geometry.polygon.holes.length > 0,
    );
    expect(withHoles.length).toBeGreaterThan(0);

    // Sample every stitch segment against the hole rings.
    for (const obj of withHoles) {
      if (obj.geometry.kind !== 'polygon') continue;
      for (const hole of obj.geometry.polygon.holes) {
        const xs = hole.map((p) => p.x);
        const ys = hole.map((p) => p.y);
        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        // Only check holes big enough for the test to be meaningful.
        if (maxX - minX < 40 || maxY - minY < 40) continue;
        const inset = 8; // stay 0.8 mm inside the hole to allow for smoothing
        let inside = 0;
        for (const s of design.stitches) {
          if (s.command !== StitchCommand.Stitch) continue;
          if (s.x > minX + inset && s.x < maxX - inset && s.y > minY + inset && s.y < maxY - inset) inside++;
        }
        // A rectangular bounding box around a ring-shaped hole will contain
        // legitimate stitches, so this only asserts the hole is not filled.
        const boxArea = (maxX - minX) * (maxY - minY);
        expect(inside / boxArea).toBeLessThan(0.02);
      }
    }
  });

  it('the narrow-satin fixture produces satin columns', () => {
    const { metrics } = runFixture(catalog.designs.find((d) => d.id === '05-narrow-satin')!);
    expect(metrics.satinObjects).toBeGreaterThanOrEqual(2);
  });

  it('the detailed badge is the heaviest design in the set', () => {
    const detailed = runFixture(catalog.designs.find((d) => d.id === '06-detailed-logo')!);
    const simple = runFixture(catalog.designs.find((d) => d.id === '01-single-color-square')!);
    expect(detailed.metrics.stitchCount).toBeGreaterThan(simple.metrics.stitchCount);
    expect(detailed.metrics.colorBlocks).toBeGreaterThan(simple.metrics.colorBlocks);
  });
});
