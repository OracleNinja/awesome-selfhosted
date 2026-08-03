/**
 * Turn EmbroideryObjects into an actual stitch stream.
 *
 * This is the single place where geometry becomes needle penetrations. The
 * design's statistics, preview, validation and PES export all read the output
 * of this module, so what you see is exactly what gets sewn.
 */

import { distance, type Point, type Polygon } from '../../domain/geometry';
import {
  DEFAULTS,
  type EmbroideryObject,
} from '../../domain/embroidery-object';
import {
  StitchCommand,
  colorChange,
  end,
  jump,
  stitch,
  type Stitch,
} from '../../domain/stitch';
import { generateFill } from './fill';
import { beanStitch, dropShortSegments, enforceMaxStitchLength, outlineStitch, runningStitch } from './running';
import { columnFromRing, generateSatin, type SatinColumn } from './satin';
import { polygonUnderlay, satinUnderlay } from './underlay';

export interface GenerateOptions {
  minStitchLength: number;
  maxStitchLength: number;
  /** Travel further than this between runs becomes a jump (and so a trim). */
  trimThreshold: number;
}

export const DEFAULT_GENERATE_OPTIONS: GenerateOptions = {
  minStitchLength: DEFAULTS.minStitchLength,
  maxStitchLength: DEFAULTS.maxStitchLength,
  trimThreshold: 60, // 6 mm
};

export interface ObjectStitchResult {
  objectId: string;
  threadIndex: number;
  /** Continuous sewing runs. Consecutive runs may need a jump between them. */
  runs: Point[][];
  /** Problems found while generating this object. */
  notes: string[];
}

/** Generate the runs for a single object. */
export function generateObjectStitches(
  obj: EmbroideryObject,
  options: GenerateOptions = DEFAULT_GENERATE_OPTIONS,
): ObjectStitchResult {
  const notes: string[] = [];
  const runs: Point[][] = [];
  const runOpts = { stitchLength: obj.stitchLength, minStitchLength: options.minStitchLength };

  switch (obj.type) {
    case 'fill': {
      const poly = requirePolygon(obj);
      if (!poly) {
        notes.push('Fill object has no polygon geometry; nothing was generated.');
        break;
      }
      runs.push(
        ...polygonUnderlay(poly, obj.underlay, obj.angle, options.minStitchLength),
      );
      const fill = generateFill(poly, {
        angle: obj.angle,
        density: obj.density,
        stitchLength: obj.stitchLength,
        minStitchLength: options.minStitchLength,
        stagger: 4,
        maxTravelStitch: options.trimThreshold,
      });
      if (fill.length === 0) {
        // The shape is thinner than one fill row. Outline it so it is not lost.
        notes.push('Shape is too narrow for a fill row; stitched as an outline instead.');
        runs.push(outlineStitch(poly.outer, runOpts));
      } else {
        runs.push(...fill);
      }
      break;
    }

    case 'satin': {
      const column = requireColumn(obj);
      if (!column) {
        notes.push('Satin object has no usable column geometry; nothing was generated.');
        break;
      }
      runs.push(...satinUnderlay(column, obj.underlay, options.minStitchLength));
      const satin = generateSatin(column, {
        density: obj.density,
        maxStitchLength: options.maxStitchLength,
        minStitchLength: options.minStitchLength,
      });
      if (satin.length >= 2) runs.push(satin);
      else notes.push('Satin column produced no stitches; it may be degenerate.');
      break;
    }

    case 'running': {
      const path = requirePath(obj);
      if (!path || path.length < 2) {
        notes.push('Running stitch object has fewer than two points.');
        break;
      }
      runs.push(runningStitch(path, runOpts));
      break;
    }

    case 'bean': {
      const path = requirePath(obj);
      if (!path || path.length < 2) {
        notes.push('Bean stitch object has fewer than two points.');
        break;
      }
      runs.push(beanStitch(path, obj.beanRepeats ?? 3, runOpts));
      break;
    }

    case 'outline': {
      const poly = requirePolygon(obj);
      if (poly) {
        runs.push(outlineStitch(poly.outer, runOpts));
        for (const hole of poly.holes) runs.push(outlineStitch(hole, runOpts));
      } else {
        const path = requirePath(obj);
        if (path && path.length >= 2) runs.push(runningStitch(path, runOpts));
        else notes.push('Outline object has no usable geometry.');
      }
      break;
    }

    case 'manual': {
      const path = requirePath(obj);
      if (!path || path.length < 2) {
        notes.push('Manual stitch object has fewer than two points.');
        break;
      }
      // Manual paths are taken literally: each point is one needle penetration.
      runs.push(path.map((p) => ({ ...p })));
      break;
    }
  }

  const cleaned = runs
    .map((run) => enforceMaxStitchLength(run, options.maxStitchLength))
    .map((run) => dropShortSegments(run, options.minStitchLength))
    .filter((run) => run.length >= 2);

  return { objectId: obj.id, threadIndex: obj.threadIndex, runs: cleaned, notes };
}

function requirePolygon(obj: EmbroideryObject): Polygon | null {
  if (obj.geometry.kind === 'polygon') return obj.geometry.polygon;
  if (obj.geometry.kind === 'path' && obj.geometry.closed && obj.geometry.points.length >= 3) {
    return { outer: obj.geometry.points, holes: [] };
  }
  return null;
}

function requirePath(obj: EmbroideryObject): Point[] | null {
  if (obj.geometry.kind === 'path') return obj.geometry.points;
  if (obj.geometry.kind === 'polygon') return [...obj.geometry.polygon.outer, obj.geometry.polygon.outer[0]];
  if (obj.geometry.kind === 'column') {
    const mid = obj.geometry.left.map((p, i) => {
      const r = obj.geometry.kind === 'column' ? obj.geometry.right[Math.min(i, obj.geometry.right.length - 1)] : p;
      return { x: (p.x + r.x) / 2, y: (p.y + r.y) / 2 };
    });
    return mid;
  }
  return null;
}

function requireColumn(obj: EmbroideryObject): SatinColumn | null {
  if (obj.geometry.kind === 'column') {
    return { left: obj.geometry.left, right: obj.geometry.right };
  }
  if (obj.geometry.kind === 'polygon') return columnFromRing(obj.geometry.polygon.outer);
  if (obj.geometry.kind === 'path' && obj.geometry.closed) return columnFromRing(obj.geometry.points);
  return null;
}

/**
 * Sequence every object into one stitch stream, inserting jumps (which the PES
 * encoder turns into trims) and colour changes.
 *
 * Objects must already be ordered so that objects sharing a thread are
 * adjacent; `optimizeOrder` does that.
 */
export function assembleStitches(
  objects: readonly EmbroideryObject[],
  options: GenerateOptions = DEFAULT_GENERATE_OPTIONS,
): { stitches: Stitch[]; notes: Map<string, string[]>; threadSequence: number[] } {
  const ordered = [...objects].filter((o) => o.visible).sort((a, b) => a.order - b.order);
  const notes = new Map<string, string[]>();
  const out: Stitch[] = [];
  const threadSequence: number[] = [];

  let current: Point | null = null;
  let currentThread: number | null = null;
  let anyStitches = false;

  for (const obj of ordered) {
    const result = generateObjectStitches(obj, options);
    if (result.notes.length) notes.set(obj.id, result.notes);
    if (result.runs.length === 0) continue;

    if (currentThread === null) {
      currentThread = obj.threadIndex;
      threadSequence.push(obj.threadIndex);
    } else if (obj.threadIndex !== currentThread) {
      const at = current ?? { x: 0, y: 0 };
      out.push(colorChange(at.x, at.y));
      currentThread = obj.threadIndex;
      threadSequence.push(obj.threadIndex);
      // A colour change always breaks the thread, so the next move is a jump.
      current = null;
    }

    let firstRunOfObject = true;
    for (const run of result.runs) {
      const start = run[0];
      const needsJump =
        current === null ||
        (firstRunOfObject && obj.forceTrimBefore) ||
        distance(current, start) > options.trimThreshold;

      let firstIndex: number;
      if (needsJump) {
        out.push(jump(start.x, start.y));
        // The jump positions the needle; the start point still gets stitched.
        firstIndex = 0;
      } else if (current && distance(current, start) < options.minStitchLength) {
        // The needle is already effectively at the run's start. Stitching to it
        // would emit a needle-damaging sub-millimetre stitch, so skip it.
        firstIndex = 1;
      } else if (current) {
        // Short hop: sew across rather than cut the thread.
        const bridge = enforceMaxStitchLength([current, start], options.maxStitchLength);
        for (let i = 1; i < bridge.length; i++) out.push(stitch(bridge[i].x, bridge[i].y));
        firstIndex = 1;
      } else {
        firstIndex = 0;
      }

      for (let i = firstIndex; i < run.length; i++) {
        out.push(stitch(run[i].x, run[i].y));
      }
      if (run.length > firstIndex) {
        current = run[run.length - 1];
        anyStitches = true;
      }
      firstRunOfObject = false;
    }
  }

  if (!anyStitches) return { stitches: [], notes, threadSequence: [] };

  const last = current ?? { x: 0, y: 0 };
  out.push(end(last.x, last.y));
  return { stitches: out, notes, threadSequence };
}

/** Total travel distance covered by jumps, in 0.1 mm units. */
export function totalJumpDistance(stitches: readonly Stitch[]): number {
  let total = 0;
  for (let i = 1; i < stitches.length; i++) {
    if (stitches[i].command === StitchCommand.Jump) {
      total += distance(stitches[i - 1], stitches[i]);
    }
  }
  return total;
}
