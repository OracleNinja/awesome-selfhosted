/**
 * EmbroideryDesign: the central document model.
 *
 * Every statistic exposed here is computed from the actual stitch array. None
 * of them are estimates typed in by hand.
 */

import {
  boundsOf,
  boundsHeight,
  boundsWidth,
  type Bounds,
  type Point,
} from './geometry';
import type { EmbroideryObject } from './embroidery-object';
import {
  colorBlocks,
  countCommand,
  countTrims,
  totalStitchLength,
  StitchCommand,
  type ColorBlock,
  type Stitch,
} from './stitch';
import type { Thread } from './thread';
import { unitsToMm } from './units';
import type { ValidationReport } from './validation';

export interface DesignMetadata {
  name: string;
  customer?: string;
  createdAt: string;
  modifiedAt: string;
  /** Free-form operator notes. */
  notes?: string;
}

export interface DesignCanvas {
  /** Target design width in 0.1 mm units. */
  width: number;
  /** Target design height in 0.1 mm units. */
  height: number;
  machineId: string;
  hoopId: string;
}

export interface DesignStats {
  bounds: Bounds;
  width: number;
  height: number;
  stitchCount: number;
  jumpCount: number;
  trimCount: number;
  colorChangeCount: number;
  colorCount: number;
  /** Thread laid down per colour, 0.1 mm units. */
  threadLengthByColor: number[];
  totalThreadLength: number;
  /** Seconds, derived from stitch count and a stitches-per-minute assumption. */
  estimatedRuntimeSeconds: number;
  minStitchLength: number;
  maxStitchLength: number;
  averageStitchLength: number;
}

export interface EmbroideryDesign {
  metadata: DesignMetadata;
  canvas: DesignCanvas;
  /** Distinct threads the design uses. One entry per physical cone. */
  threadPalette: Thread[];
  objects: EmbroideryObject[];
  /** Generated stitch stream. Always ends with an End command when non-empty. */
  stitches: Stitch[];
  /**
   * Palette index used by each colour block, in sew order. A design that
   * leaves a colour and returns to it has more blocks than palette entries, so
   * this is NOT the identity mapping and must not be assumed to be.
   * Null on designs imported from a stitch file, which have no objects to
   * generate from and carry one palette entry per block already.
   */
  colorSequence: number[] | null;
  validation: ValidationReport | null;
}

/**
 * Stitching speed used for runtime estimates. Home machines like the SE700 run
 * up to 850 spm; real-world average including colour changes is lower, so this
 * is deliberately conservative and reported as an estimate.
 */
export const ASSUMED_STITCHES_PER_MINUTE = 650;
/** Extra seconds budgeted per colour change on a single-needle machine. */
export const SECONDS_PER_COLOR_CHANGE = 45;

export function emptyDesign(metadata: DesignMetadata, canvas: DesignCanvas): EmbroideryDesign {
  return {
    metadata,
    canvas,
    threadPalette: [],
    objects: [],
    stitches: [],
    colorSequence: [],
    validation: null,
  };
}

export function stitchPoints(stitches: readonly Stitch[]): Point[] {
  return stitches
    .filter((s) => s.command === StitchCommand.Stitch)
    .map((s) => ({ x: s.x, y: s.y }));
}

export function designBounds(design: EmbroideryDesign): Bounds {
  return boundsOf(stitchPoints(design.stitches));
}

export function designColorBlocks(design: EmbroideryDesign): ColorBlock[] {
  return colorBlocks(design.stitches);
}

export function computeStats(design: EmbroideryDesign): DesignStats {
  const stitches = design.stitches;
  const points = stitchPoints(stitches);
  const bounds = boundsOf(points);

  const stitchCount = points.length;
  const jumpCount = countCommand(stitches, StitchCommand.Jump);
  const trimCount = countTrims(stitches);
  const colorChangeCount = countCommand(stitches, StitchCommand.ColorChange);

  const blocks = colorBlocks(stitches);
  const threadLengthByColor = blocks.map((b) =>
    totalStitchLength(stitches.slice(b.start, b.endExclusive)),
  );
  const total = threadLengthByColor.reduce((a, b) => a + b, 0);

  let min = Infinity;
  let max = 0;
  let sum = 0;
  let n = 0;
  for (let i = 1; i < stitches.length; i++) {
    const a = stitches[i - 1];
    const b = stitches[i];
    if (a.command !== StitchCommand.Stitch || b.command !== StitchCommand.Stitch) continue;
    const d = Math.hypot(b.x - a.x, b.y - a.y);
    if (d < min) min = d;
    if (d > max) max = d;
    sum += d;
    n++;
  }

  const runtime =
    (stitchCount / ASSUMED_STITCHES_PER_MINUTE) * 60 + colorChangeCount * SECONDS_PER_COLOR_CHANGE;

  return {
    bounds,
    width: boundsWidth(bounds),
    height: boundsHeight(bounds),
    stitchCount,
    jumpCount,
    trimCount,
    colorChangeCount,
    colorCount: blocks.length,
    threadLengthByColor,
    totalThreadLength: total,
    estimatedRuntimeSeconds: runtime,
    minStitchLength: n > 0 ? min : 0,
    maxStitchLength: max,
    averageStitchLength: n > 0 ? sum / n : 0,
  };
}

export function formatRuntime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/**
 * Thread consumption estimate. Bobbin thread roughly matches top thread on a
 * lockstitch machine, so total consumption is reported as roughly double the
 * stitched path length; this is explicitly an estimate and labelled as such in
 * the UI.
 */
export function estimateThreadMetres(threadLengthUnits: number): number {
  return (unitsToMm(threadLengthUnits) / 1000) * 2;
}
