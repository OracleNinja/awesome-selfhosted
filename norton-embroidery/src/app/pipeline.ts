/**
 * Application layer: the one path from artwork to a validated design.
 *
 * The UI and the test-suite both call these functions, so what the tests prove
 * is what the operator gets.
 */

import { emptyDesign, type DesignCanvas, type DesignMetadata, type EmbroideryDesign } from '../domain/design';
import { mapGeometry, objectPoints, type EmbroideryObject } from '../domain/embroidery-object';
import { boundsOf } from '../domain/geometry';
import { defaultHoop, getHoop, getMachine, type MachineProfile } from '../domain/machine';
import { PEC_THREADS, type Thread } from '../domain/thread';
import { mmToUnits } from '../domain/units';
import {
  analyzeArtwork,
  DEFAULT_ANALYSIS_OPTIONS,
  type AnalysisOptions,
  type ArtworkAnalysis,
} from '../processing/image/analysis';
import type { RasterImage } from '../processing/image/raster';
import { digitize, defaultDigitizeOptions, type DigitizeOptions, type DigitizeResult } from '../processing/digitize/digitizer';
import { assembleStitches, DEFAULT_GENERATE_OPTIONS, type GenerateOptions } from '../processing/stitches/generate';
import { optimizeOrder } from '../processing/optimize/order';
import { validateDesign } from '../processing/validate/validate-design';

export interface NewProjectInput {
  name: string;
  customer?: string;
  machineId: string;
  hoopId: string;
  /** Design width in 0.1 mm units. */
  width: number;
  /** Design height in 0.1 mm units. */
  height: number;
}

export function createProject(input: NewProjectInput): { design: EmbroideryDesign; machine: MachineProfile } {
  const machine = getMachine(input.machineId);
  if (!machine) throw new Error(`Unknown machine profile "${input.machineId}".`);
  const hoop = getHoop(machine, input.hoopId) ?? defaultHoop(machine);

  const now = new Date().toISOString();
  const metadata: DesignMetadata = {
    name: input.name,
    customer: input.customer,
    createdAt: now,
    modifiedAt: now,
  };
  const canvas: DesignCanvas = {
    width: input.width,
    height: input.height,
    machineId: machine.id,
    hoopId: hoop.id,
  };
  return { design: emptyDesign(metadata, canvas), machine };
}

/** Scale artwork proportionally into the requested design box. */
export function fitArtworkToCanvas(
  image: { width: number; height: number },
  canvas: { width: number; height: number },
): { width: number; height: number } {
  const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
  return {
    width: Math.round(image.width * scale),
    height: Math.round(image.height * scale),
  };
}

export function analyze(
  image: RasterImage,
  canvas: { width: number; height: number },
  overrides: Partial<AnalysisOptions> = {},
): ArtworkAnalysis {
  const fitted = fitArtworkToCanvas(image, canvas);
  const options: AnalysisOptions = {
    ...DEFAULT_ANALYSIS_OPTIONS,
    targetWidthUnits: fitted.width,
    targetHeightUnits: fitted.height,
    ...overrides,
  };
  return analyzeArtwork(image, options);
}

export interface DigitizeAndGenerateResult {
  design: EmbroideryDesign;
  digitize: DigitizeResult;
  /** Per-object notes produced while generating stitches. */
  generationNotes: Map<string, string[]>;
}

/**
 * Run automatic digitizing and produce stitches for the result, then validate.
 */
export function digitizeAndGenerate(
  design: EmbroideryDesign,
  analysis: ArtworkAnalysis,
  machine: MachineProfile,
  options: {
    threads?: Thread[];
    digitize?: Partial<DigitizeOptions>;
    generate?: Partial<GenerateOptions>;
  } = {},
): DigitizeAndGenerateResult {
  const threads = options.threads ?? PEC_THREADS;
  const fitted = fitArtworkToCanvas(
    { width: analysis.analysisDimensions.width, height: analysis.analysisDimensions.height },
    { width: design.canvas.width, height: design.canvas.height },
  );

  const digitizeOptions: DigitizeOptions = {
    ...defaultDigitizeOptions(fitted.width, fitted.height, threads),
    ...options.digitize,
  };

  const result = digitize(analysis, digitizeOptions);

  // Place the result in the middle of the hoop. Digitizing works in a box
  // anchored at the origin, which would otherwise leave every design pinned to
  // the top-left corner of the hoop preview.
  const hoop = getHoop(machine, design.canvas.hoopId) ?? defaultHoop(machine);
  const centred = centerObjectsInHoop(result.objects, hoop.width, hoop.height);

  const next: EmbroideryDesign = {
    ...design,
    threadPalette: result.threadPalette,
    objects: centred,
  };

  const regenerated = regenerateWithNotes(next, machine, options.generate);
  return { design: regenerated.design, digitize: result, generationNotes: regenerated.notes };
}

/**
 * Re-run stitch generation and validation for the design's current objects.
 * Called after every edit, which is what keeps preview, stats and export in
 * step with what the operator sees.
 */
export function regenerateWithNotes(
  design: EmbroideryDesign,
  machine: MachineProfile,
  generateOverrides: Partial<GenerateOptions> = {},
): { design: EmbroideryDesign; notes: Map<string, string[]> } {
  const options: GenerateOptions = {
    ...DEFAULT_GENERATE_OPTIONS,
    maxStitchLength: Math.min(DEFAULT_GENERATE_OPTIONS.maxStitchLength, machine.maxStitchLength),
    ...generateOverrides,
  };

  const { stitches, notes } = assembleStitches(design.objects, options);

  const withStitches: EmbroideryDesign = {
    ...design,
    stitches,
    metadata: { ...design.metadata, modifiedAt: new Date().toISOString() },
  };
  return { design: { ...withStitches, validation: validateDesign(withStitches, machine) }, notes };
}

export function regenerate(
  design: EmbroideryDesign,
  machine: MachineProfile,
  generateOverrides: Partial<GenerateOptions> = {},
): EmbroideryDesign {
  return regenerateWithNotes(design, machine, generateOverrides).design;
}

/** Shift every object so the group is centred in the hoop field. */
function centerObjectsInHoop(
  objects: readonly EmbroideryObject[],
  hoopWidth: number,
  hoopHeight: number,
): EmbroideryObject[] {
  const all = objects.flatMap(objectPoints);
  if (all.length === 0) return [...objects];
  const b = boundsOf(all);
  const dx = hoopWidth / 2 - (b.minX + b.maxX) / 2;
  const dy = hoopHeight / 2 - (b.minY + b.maxY) / 2;
  if (dx === 0 && dy === 0) return [...objects];
  return objects.map((o) => ({
    ...o,
    geometry: mapGeometry(o.geometry, (p) => ({ x: p.x + dx, y: p.y + dy })),
  }));
}

/** Re-run the order optimiser and regenerate. */
export function reorderAndRegenerate(design: EmbroideryDesign, machine: MachineProfile): EmbroideryDesign {
  const ordered = optimizeOrder(design.objects);
  return regenerate({ ...design, objects: ordered.objects }, machine);
}

/** Replace one object and regenerate the design. */
export function updateObject(
  design: EmbroideryDesign,
  objectId: string,
  patch: Partial<EmbroideryObject>,
  machine: MachineProfile,
): EmbroideryDesign {
  const objects = design.objects.map((o) => (o.id === objectId ? { ...o, ...patch } : o));
  return regenerate({ ...design, objects }, machine);
}

/** Convenience for tests and for the "new project" defaults. */
export const DEFAULT_DESIGN_SIZE = {
  width: mmToUnits(90),
  height: mmToUnits(90),
};
