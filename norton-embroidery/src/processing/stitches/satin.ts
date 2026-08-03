/**
 * Satin column generation.
 *
 * A satin column is defined by two rails walked in the same direction. The
 * needle alternates between them, so the thread lies across the column and
 * gives the glossy raised look used for lettering, borders and thin shapes.
 */

import {
  distance,
  lerp,
  pathLength,
  resamplePath,
  type Point,
  type Ring,
} from '../../domain/geometry';

export interface SatinOptions {
  /** Spacing between needle penetrations along the column, 0.1 mm units. */
  density: number;
  /** Longest single crossing stitch before it gets split, 0.1 mm units. */
  maxStitchLength: number;
  minStitchLength: number;
}

export const DEFAULT_SATIN_OPTIONS: SatinOptions = {
  density: 4,
  maxStitchLength: 120,
  minStitchLength: 6,
};

export interface SatinColumn {
  left: Point[];
  right: Point[];
}

/** Zigzag the needle across the column. */
export function generateSatin(column: SatinColumn, options: SatinOptions): Point[] {
  const { left, right } = column;
  if (left.length < 2 || right.length < 2) return [];

  const avgLength = (pathLength(left) + pathLength(right)) / 2;
  if (avgLength <= 0) return [];

  // The needle alternates rails and advances half a step each crossing, so
  // sampling at twice the rate puts consecutive penetrations on the SAME rail
  // exactly `density` apart, which is what density means for a satin column.
  const perRail = Math.max(2, Math.round(avgLength / Math.max(1, options.density)));
  const samples = perRail * 2;
  const l = resamplePath(left, samples);
  const r = resamplePath(right, samples);

  const out: Point[] = [l[0]];
  for (let i = 1; i < samples; i++) {
    const target = i % 2 === 0 ? l[i] : r[i];
    pushCrossing(out, out[out.length - 1], target, options.maxStitchLength);
  }
  return out;
}

function pushCrossing(out: Point[], from: Point, to: Point, maxLength: number): void {
  const d = distance(from, to);
  if (d <= maxLength) {
    out.push(to);
    return;
  }
  // Wide column: split the crossing so no stitch exceeds the machine limit.
  const steps = Math.ceil(d / maxLength);
  for (let s = 1; s <= steps; s++) out.push(lerp(from, to, s / steps));
}

/**
 * Derive a satin column from the outline of a long narrow shape.
 *
 * The two ends of the shape are found as the pair of outline points that are
 * farthest apart; splitting the ring there gives the two rails. This is
 * reliable for the strokes, bars and lettering that satin is used for, and
 * returns null when the shape is not elongated enough for the split to be
 * meaningful.
 */
export function columnFromRing(ring: Ring): SatinColumn | null {
  if (ring.length < 4) return null;

  // Farthest-pair search. Rings from tracing are already simplified, so the
  // quadratic scan is bounded and cheap.
  let bestA = 0;
  let bestB = 0;
  let bestD = -1;
  for (let i = 0; i < ring.length; i++) {
    for (let j = i + 1; j < ring.length; j++) {
      const d = distance(ring[i], ring[j]);
      if (d > bestD) {
        bestD = d;
        bestA = i;
        bestB = j;
      }
    }
  }
  if (bestD <= 0) return null;

  const first: Point[] = [];
  for (let i = bestA; i !== bestB; i = (i + 1) % ring.length) first.push(ring[i]);
  first.push(ring[bestB]);

  const second: Point[] = [];
  for (let i = bestB; i !== bestA; i = (i + 1) % ring.length) second.push(ring[i]);
  second.push(ring[bestA]);

  if (first.length < 2 || second.length < 2) return null;

  // Walk both rails in the same direction so the zigzag does not twist.
  return { left: first, right: second.reverse() };
}

/** Centreline of a column, used for underlay and for thin running-stitch shapes. */
export function columnCenterline(column: SatinColumn, samples = 0): Point[] {
  const n = samples || Math.max(2, Math.max(column.left.length, column.right.length));
  const l = resamplePath(column.left, n);
  const r = resamplePath(column.right, n);
  return l.map((p, i) => ({ x: (p.x + r[i].x) / 2, y: (p.y + r[i].y) / 2 }));
}

/**
 * Width of the column at evenly spaced samples along its body.
 *
 * The very tips are excluded on purpose: rails derived from an outline meet at
 * each end, so the width there is zero by construction and says nothing about
 * whether the column can actually be sewn. What matters is the body.
 */
export function columnWidths(column: SatinColumn, samples = 24): number[] {
  const l = resamplePath(column.left, samples + 2).slice(1, -1);
  const r = resamplePath(column.right, samples + 2).slice(1, -1);
  return l.map((p, i) => distance(p, r[i]));
}
