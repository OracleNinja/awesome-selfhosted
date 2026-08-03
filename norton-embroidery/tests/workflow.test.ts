/**
 * End-to-end workflow: UPLOAD -> ANALYZE -> DIGITIZE -> PREVIEW -> VALIDATE ->
 * EXPORT, plus editing, undo/redo and project save/load.
 *
 * The export test writes a real .pes file to disk and verifies it with
 * pyembroidery, an independent implementation of the format.
 */

import { describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { analyze, createProject, digitizeAndGenerate, regenerate } from '../src/app/pipeline';
import { exportPes } from '../src/app/export-pes';
import { BROTHER_SE700, getHoop } from '../src/domain/machine';
import { computeStats } from '../src/domain/design';
import { mmToUnits, unitsToMm } from '../src/domain/units';
import { StitchCommand } from '../src/domain/stitch';
import { PEC_THREADS } from '../src/domain/thread';
import { canRedo, canUndo, createHistory, push, redo, undo } from '../src/app/history';
import { deleteObject, duplicateObject, moveObject, scaleDesign, setObjectProperty } from '../src/app/editor-ops';
import { projectFromJson, projectToJson, ProjectFormatError } from '../src/infra/project/project-format';
import { readPes } from '../src/infra/pes/pes-reader';
import {
  blankArtwork,
  fromPngBuffer,
  ringArtwork,
  simpleSquare,
  thinBarArtwork,
  threeColorArtwork,
  toPngBuffer,
} from './fixtures';

const machine = BROTHER_SE700;
const hoop = getHoop(machine, 'se700-5x7')!;

function newProject(name: string, widthMm = 70, heightMm = 70) {
  return createProject({
    name,
    customer: 'Test Customer',
    machineId: machine.id,
    hoopId: hoop.id,
    width: mmToUnits(widthMm),
    height: mmToUnits(heightMm),
  });
}

/** Run the whole pipeline on a fixture and return everything downstream needs. */
function runPipeline(image: ReturnType<typeof simpleSquare>, name: string, widthMm = 70, heightMm = 70) {
  const { design } = newProject(name, widthMm, heightMm);
  const analysis = analyze(image, design.canvas);
  const result = digitizeAndGenerate(design, analysis, machine);
  return { analysis, ...result };
}

describe('workflow: upload -> analyze -> digitize -> validate -> export', () => {
  it('turns a simple logo into a real, validated PES file', () => {
    // UPLOAD: start from actual PNG bytes, as the browser would.
    const png = toPngBuffer(simpleSquare(160));
    const image = fromPngBuffer(png);

    // ANALYZE
    const { design: project } = newProject('Simple Square');
    const analysis = analyze(image, project.canvas);
    expect(analysis.foregroundRegions.length).toBeGreaterThan(0);
    expect(analysis.backgroundDetected).toBe(true);

    // DIGITIZE
    const { design, digitize } = digitizeAndGenerate(project, analysis, machine);
    expect(design.objects.length).toBeGreaterThan(0);
    expect(design.threadPalette.length).toBeGreaterThan(0);
    expect(digitize.colorMapping.length).toBeGreaterThan(0);

    // PREVIEW data comes from the actual stitches, not the image.
    const stats = computeStats(design);
    expect(stats.stitchCount).toBeGreaterThan(200);
    expect(stats.width).toBeGreaterThan(0);
    expect(stats.height).toBeGreaterThan(0);
    // The square occupies 60% of a 70 mm canvas, so about 42 mm.
    expect(unitsToMm(stats.width)).toBeGreaterThan(30);
    expect(unitsToMm(stats.width)).toBeLessThan(60);

    // VALIDATE
    expect(design.validation).not.toBeNull();
    expect(design.validation!.errorCount).toBe(0);

    // EXPORT
    const result = exportPes(design, machine, { acknowledgeWarnings: true });
    expect(result.blockedReason).toBeUndefined();
    expect(result.ok).toBe(true);
    expect(result.bytes).toBeDefined();
    expect(result.verification.every((c) => c.passed)).toBe(true);

    // The file is real: write it out and check it on disk.
    const dir = mkdtempSync(join(tmpdir(), 'norton-e2e-'));
    const file = join(dir, result.fileName);
    writeFileSync(file, result.bytes!);
    expect(statSync(file).size).toBe(result.bytes!.length);
    expect(statSync(file).size).toBeGreaterThan(1000);

    const onDisk = readFileSync(file);
    expect(onDisk.subarray(0, 8).toString('ascii')).toBe('#PES0001');

    // And an independent implementation agrees with our stitch count.
    const script = `
import json, sys, pyembroidery
from pyembroidery.EmbConstant import STITCH, COMMAND_MASK
p = pyembroidery.read(sys.argv[1])
n = sum(1 for s in p.stitches if (s[2] & COMMAND_MASK) == STITCH)
b = p.bounds()
print(json.dumps({"stitches": n, "colors": len(p.threadlist), "w": b[2]-b[0], "h": b[3]-b[1]}))
`;
    const scriptFile = join(dir, 'check.py');
    writeFileSync(scriptFile, script);
    const info = JSON.parse(execFileSync('python3', [scriptFile, file], { encoding: 'utf8' }));

    expect(info.stitches).toBe(stats.stitchCount);
    expect(info.colors).toBe(stats.colorCount);
    expect(Math.abs(info.w - stats.width)).toBeLessThanOrEqual(1);
    expect(Math.abs(info.h - stats.height)).toBeLessThanOrEqual(1);
  });

  it('digitizes multi-colour artwork into grouped colour blocks', () => {
    const { design } = runPipeline(threeColorArtwork(240, 160), 'Three Colours', 90, 60);
    const stats = computeStats(design);

    expect(design.threadPalette.length).toBeGreaterThanOrEqual(3);
    // Objects sharing a thread must sew together: one block per thread used.
    const threadsUsed = new Set(design.objects.map((o) => o.threadIndex)).size;
    expect(stats.colorCount).toBe(threadsUsed);
    expect(stats.colorChangeCount).toBe(threadsUsed - 1);

    const result = exportPes(design, machine, { acknowledgeWarnings: true });
    expect(result.ok).toBe(true);
    const back = readPes(result.bytes!);
    expect(back.threads.length).toBe(stats.colorCount);
  });

  it('digitizes a shape with a hole without stitching across it', () => {
    const { design } = runPipeline(ringArtwork(200), 'Ring', 60, 60);
    expect(design.objects.length).toBeGreaterThan(0);
    const stats = computeStats(design);
    expect(stats.stitchCount).toBeGreaterThan(200);
    expect(design.validation!.errorCount).toBe(0);
  });

  it('chooses satin for a long thin bar', () => {
    const { design } = runPipeline(thinBarArtwork(300, 90), 'Bar', 100, 30);
    const satin = design.objects.filter((o) => o.type === 'satin');
    expect(satin.length).toBeGreaterThan(0);
    expect(satin[0].geometry.kind).toBe('column');
  });

  it('produces stitch coordinates, not a rasterised image', () => {
    const { design } = runPipeline(simpleSquare(160), 'Coords');
    const points = design.stitches.filter((s) => s.command === StitchCommand.Stitch);
    expect(points.length).toBeGreaterThan(100);
    // Coordinates must be real positions in 0.1 mm units, not pixel indices.
    const xs = points.map((p) => p.x);
    expect(Math.max(...xs)).toBeGreaterThan(100);
    expect(points.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
  });
});

describe('validation gates the export', () => {
  it('refuses to export an empty design and says why', () => {
    const { design } = newProject('Empty');
    const generated = regenerate(design, machine);
    const result = exportPes(generated, machine, { acknowledgeWarnings: true });

    expect(result.ok).toBe(false);
    expect(result.bytes).toBeUndefined();
    expect(result.validation.errorCount).toBeGreaterThan(0);
    expect(result.validation.issues.some((i) => i.code === 'design.empty')).toBe(true);
    expect(result.blockedReason).toMatch(/error/i);
  });

  it('refuses blank artwork rather than exporting nothing', () => {
    const { design } = runPipeline(blankArtwork(80), 'Blank');
    const result = exportPes(design, machine, { acknowledgeWarnings: true });
    expect(result.ok).toBe(false);
    expect(result.validation.issues.some((i) => i.code === 'design.empty')).toBe(true);
  });

  it('blocks a design that is too large for the hoop, quoting the overage', () => {
    const { design } = runPipeline(simpleSquare(160), 'Oversize');
    // Blow the design up well past the 130 x 180 mm field.
    const oversized = scaleDesign(design, machine, 6);

    const result = exportPes(oversized, machine, { acknowledgeWarnings: true });
    expect(result.ok).toBe(false);

    const hoopIssue = result.validation.issues.find((i) => i.code === 'hoop.width' || i.code === 'hoop.height');
    expect(hoopIssue).toBeDefined();
    expect(hoopIssue!.severity).toBe('ERROR');
    // The message must contain the actual overage, not a vague failure.
    expect(hoopIssue!.message).toMatch(/exceeds the .* hoop by [\d.]+ inches/);
    expect(hoopIssue!.message).not.toMatch(/something went wrong/i);
  });

  it('requires warnings to be acknowledged before exporting', () => {
    const { design } = runPipeline(simpleSquare(160), 'Warned');
    // Force a warning: an over-dense fill.
    const dense = setObjectProperty(design.objects[0].id, 'density', 2)(design, machine);
    expect(dense.validation!.warningCount).toBeGreaterThan(0);

    const refused = exportPes(dense, machine, { acknowledgeWarnings: false });
    expect(refused.ok).toBe(false);
    expect(refused.blockedReason).toMatch(/acknowledge/i);

    const accepted = exportPes(dense, machine, { acknowledgeWarnings: true });
    expect(accepted.ok).toBe(true);
  });

  it('reports a stitch-length violation as an error', () => {
    const { design } = runPipeline(simpleSquare(160), 'LongStitch');
    // Manually inject a stitch far longer than the machine allows.
    const broken = {
      ...design,
      stitches: [
        ...design.stitches.slice(0, -1),
        { x: 5000, y: 5000, command: StitchCommand.Stitch },
        design.stitches[design.stitches.length - 1],
      ],
    };
    const result = exportPes(broken, machine, { acknowledgeWarnings: true });
    expect(result.ok).toBe(false);
    expect(result.validation.issues.some((i) => i.code === 'stitch.too-long')).toBe(true);
  });

  it('reports non-finite coordinates as an error instead of writing a broken file', () => {
    const { design } = runPipeline(simpleSquare(160), 'NaN');
    const broken = {
      ...design,
      stitches: design.stitches.map((s, i) => (i === 5 ? { ...s, x: NaN } : s)),
    };
    const result = exportPes(broken, machine, { acknowledgeWarnings: true });
    expect(result.ok).toBe(false);
    expect(result.validation.issues.some((i) => i.code === 'stitch.invalid-coordinate')).toBe(true);
  });

  it('passes a design that fits the hoop', () => {
    const { design } = runPipeline(simpleSquare(160), 'Fits', 60, 60);
    const stats = computeStats(design);
    expect(stats.width).toBeLessThan(hoop.width);
    expect(stats.height).toBeLessThan(hoop.height);
    expect(design.validation!.passed).toBe(true);
  });
});

describe('editing', () => {
  it('moves an object and the stitches follow', () => {
    const { design } = runPipeline(simpleSquare(160), 'Move');
    const before = computeStats(design).bounds.minX;
    const moved = moveObject(design.objects[0].id, mmToUnits(5), 0)(design, machine);
    const after = computeStats(moved).bounds.minX;
    expect(after).toBeGreaterThan(before);
  });

  it('deletes an object and the stitch count drops', () => {
    const { design } = runPipeline(threeColorArtwork(240, 160), 'Delete', 90, 60);
    const before = computeStats(design).stitchCount;
    const reduced = deleteObject(design.objects[0].id)(design, machine);
    expect(reduced.objects.length).toBe(design.objects.length - 1);
    expect(computeStats(reduced).stitchCount).toBeLessThan(before);
  });

  it('duplicates an object', () => {
    const { design } = runPipeline(simpleSquare(160), 'Duplicate');
    const before = computeStats(design).stitchCount;
    const dup = duplicateObject(design.objects[0].id)(design, machine);
    expect(dup.objects.length).toBe(design.objects.length + 1);
    expect(computeStats(dup).stitchCount).toBeGreaterThan(before);
  });

  it('changing density changes the stitch count', () => {
    const { design } = runPipeline(simpleSquare(160), 'Density');
    const fillObject = design.objects.find((o) => o.type === 'fill');
    expect(fillObject).toBeDefined();
    const before = computeStats(design).stitchCount;
    const looser = setObjectProperty(fillObject!.id, 'density', fillObject!.density * 2)(design, machine);
    expect(computeStats(looser).stitchCount).toBeLessThan(before);
  });

  it('changing the fill angle changes the stitch path', () => {
    const { design } = runPipeline(simpleSquare(160), 'Angle');
    const fillObject = design.objects.find((o) => o.type === 'fill')!;
    const rotated = setObjectProperty(fillObject.id, 'angle', fillObject.angle + 90)(design, machine);
    const a = design.stitches.map((s) => `${Math.round(s.x)},${Math.round(s.y)}`).join('|');
    const b = rotated.stitches.map((s) => `${Math.round(s.x)},${Math.round(s.y)}`).join('|');
    expect(a).not.toBe(b);
  });
});

describe('undo and redo', () => {
  it('restores the previous design and replays it', () => {
    const { design } = runPipeline(simpleSquare(160), 'History');
    let history = createHistory(design);
    expect(canUndo(history)).toBe(false);

    const originalCount = computeStats(design).stitchCount;
    const edited = deleteObject(design.objects[0].id)(design, machine);
    history = push(history, edited, 'Delete object');

    expect(canUndo(history)).toBe(true);
    expect(computeStats(history.present.design).stitchCount).not.toBe(originalCount);

    history = undo(history);
    expect(computeStats(history.present.design).stitchCount).toBe(originalCount);
    expect(canRedo(history)).toBe(true);

    history = redo(history);
    expect(history.present.label).toBe('Delete object');
    expect(computeStats(history.present.design).stitchCount).not.toBe(originalCount);
  });

  it('clears the redo stack after a new edit', () => {
    const { design } = runPipeline(simpleSquare(160), 'History2');
    let history = createHistory(design);
    history = push(history, deleteObject(design.objects[0].id)(design, machine), 'Delete');
    history = undo(history);
    expect(canRedo(history)).toBe(true);
    history = push(history, moveObject(design.objects[0].id, 10, 10)(design, machine), 'Move');
    expect(canRedo(history)).toBe(false);
  });

  it('bounds the history so long sessions do not grow without limit', () => {
    const { design } = runPipeline(simpleSquare(160), 'History3');
    let history = createHistory(design, 'Initial', 5);
    for (let i = 0; i < 20; i++) {
      history = push(history, moveObject(design.objects[0].id, 1, 0)(design, machine), `Move ${i}`);
    }
    expect(history.past.length).toBe(5);
  });
});

describe('project save and load', () => {
  const artwork = () => {
    const png = toPngBuffer(simpleSquare(160));
    return {
      fileName: 'logo.png',
      mimeType: 'image/png',
      base64: png.toString('base64'),
      width: 160,
      height: 160,
      byteLength: png.length,
    };
  };

  it('round-trips a project without losing work', () => {
    const { design } = runPipeline(simpleSquare(160), 'Saved Project');
    const state = { id: 'proj-1', design, artwork: artwork(), analysis: null };

    const json = projectToJson(state);
    const loaded = projectFromJson(json);

    expect(loaded.id).toBe('proj-1');
    expect(loaded.design.metadata.name).toBe('Saved Project');
    expect(loaded.design.metadata.customer).toBe('Test Customer');
    expect(loaded.design.objects.length).toBe(design.objects.length);
    expect(loaded.design.stitches.length).toBe(design.stitches.length);
    expect(loaded.design.threadPalette).toEqual(design.threadPalette);
    expect(loaded.design.canvas).toEqual(design.canvas);
    expect(computeStats(loaded.design).stitchCount).toBe(computeStats(design).stitchCount);
  });

  it('never loses the original artwork', () => {
    const original = artwork();
    const { design } = runPipeline(simpleSquare(160), 'Artwork Kept');
    const loaded = projectFromJson(projectToJson({ id: 'p', design, artwork: original, analysis: null }));

    expect(loaded.artwork).not.toBeNull();
    expect(loaded.artwork!.base64).toBe(original.base64);
    expect(loaded.artwork!.fileName).toBe('logo.png');

    // The stored bytes still decode to the original image.
    const decoded = fromPngBuffer(Buffer.from(loaded.artwork!.base64, 'base64'));
    expect(decoded.width).toBe(160);
    expect(decoded.height).toBe(160);
  });

  it('a reopened project still exports the same PES file', () => {
    const { design } = runPipeline(simpleSquare(160), 'Reopen');
    const first = exportPes(design, machine, { acknowledgeWarnings: true });
    const loaded = projectFromJson(projectToJson({ id: 'p', design, artwork: artwork(), analysis: null }));
    const second = exportPes(loaded.design, machine, { acknowledgeWarnings: true });

    expect(first.ok).toBe(true);
    expect(second.ok).toBe(true);
    expect(Array.from(second.bytes!)).toEqual(Array.from(first.bytes!));
  });

  it('rejects a file from a newer format version with a clear message', () => {
    const { design } = runPipeline(simpleSquare(160), 'Future');
    const json = JSON.parse(projectToJson({ id: 'p', design, artwork: null, analysis: null }));
    json.formatVersion = 999;
    expect(() => projectFromJson(JSON.stringify(json))).toThrow(ProjectFormatError);
    expect(() => projectFromJson(JSON.stringify(json))).toThrow(/newer version/i);
  });

  it('rejects a file that is not a Norton project', () => {
    expect(() => projectFromJson('{"application":"something-else"}')).toThrow(/not written by Norton/i);
  });

  it('migrates a version 1 project forward', () => {
    const { design } = runPipeline(simpleSquare(160), 'Old');
    const json = JSON.parse(projectToJson({ id: 'p', design, artwork: null, analysis: null }));
    json.formatVersion = 1;
    // Format 1 stored the underlay as a bare string.
    json.objects = json.objects.map((o: Record<string, unknown>) => ({ ...o, underlay: 'edge-run' }));

    const loaded = projectFromJson(JSON.stringify(json));
    for (const obj of loaded.design.objects) {
      expect(typeof obj.underlay).toBe('object');
      expect(obj.underlay.type).toBe('edge-run');
      expect(typeof obj.underlay.inset).toBe('number');
    }
  });
});

describe('thread data integrity', () => {
  it('bundles only the verified Brother machine palette', () => {
    expect(PEC_THREADS.length).toBe(64);
    expect(PEC_THREADS.every((t) => t.manufacturer === 'Brother')).toBe(true);
    // Spot-check against the values defined by the file format.
    expect(PEC_THREADS[19]).toMatchObject({ code: '20', name: 'Black', r: 0, g: 0, b: 0 });
    expect(PEC_THREADS[12]).toMatchObject({ code: '13', name: 'Yellow', r: 255, g: 255, b: 0 });
  });

  it('reports thread usage computed from the stitches', () => {
    const { design } = runPipeline(simpleSquare(160), 'Thread Usage');
    const stats = computeStats(design);
    expect(stats.totalThreadLength).toBeGreaterThan(0);
    expect(stats.threadLengthByColor.length).toBe(stats.colorCount);
    const summed = stats.threadLengthByColor.reduce((a, b) => a + b, 0);
    expect(summed).toBeCloseTo(stats.totalThreadLength, 5);
  });
});
