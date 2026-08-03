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
 * The outline is projected onto the shape's principal axis. The small caps at
 * each end of that axis are dropped, and the two arcs left between them become
 * the rails. Dropping the caps is what keeps a bar's ends square: if the caps
 * were folded into the rails, the column would taper to a point at each end and
 * a rectangular bar would sew as a lens.
 *
 * Falls back to splitting at the two most distant outline points when the
 * projection does not produce exactly two arcs, and returns null when the shape
 * cannot be read as a column at all.
 */
export function columnFromRing(ring: Ring): SatinColumn | null {
  if (ring.length < 4) return null;
  return columnFromPrincipalAxis(ring) ?? columnFromFarthestPair(ring);
}

/** Fraction of the principal-axis extent treated as an end cap. */
const END_CAP_FRACTION = 0.06;

function columnFromPrincipalAxis(ring: Ring): SatinColumn | null {
  const n = ring.length;
  let mx = 0;
  let my = 0;
  for (const p of ring) {
    mx += p.x;
    my += p.y;
  }
  mx /= n;
  my /= n;

  // Principal axis of the outline points.
  let sxx = 0;
  let sxy = 0;
  let syy = 0;
  for (const p of ring) {
    const dx = p.x - mx;
    const dy = p.y - my;
    sxx += dx * dx;
    sxy += dx * dy;
    syy += dy * dy;
  }
  const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const ux = Math.cos(theta);
  const uy = Math.sin(theta);

  const t = ring.map((p) => (p.x - mx) * ux + (p.y - my) * uy);
  const tMin = Math.min(...t);
  const tMax = Math.max(...t);
  const range = tMax - tMin;
  if (range <= 0) return null;

  const lowCut = tMin + range * END_CAP_FRACTION;
  const highCut = tMax - range * END_CAP_FRACTION;
  const isCap = t.map((v) => v <= lowCut || v >= highCut);

  if (isCap.every((c) => !c) || isCap.every((c) => c)) return null;

  // Walk the ring once and collect the maximal runs of non-cap points.
  const start = isCap.findIndex((c) => c);
  if (start < 0) return null;
  const arcs: Point[][] = [];
  let currentArc: Point[] | null = null;
  for (let k = 0; k < n; k++) {
    const i = (start + k) % n;
    if (isCap[i]) {
      if (currentArc && currentArc.length >= 2) arcs.push(currentArc);
      currentArc = null;
    } else {
      if (!currentArc) currentArc = [];
      currentArc.push(ring[i]);
    }
  }
  if (currentArc && currentArc.length >= 2) arcs.push(currentArc);

  if (arcs.length !== 2) return null;

  // Both rails must run the same way along the axis, or the zigzag twists.
  const project = (p: Point): number => (p.x - mx) * ux + (p.y - my) * uy;
  const orient = (arc: Point[]): Point[] =>
    project(arc[0]) <= project(arc[arc.length - 1]) ? arc : [...arc].reverse();

  /**
   * Slide each rail end along the axis until it reaches the shape's true
   * extent. Without this the column would stop short of the caps that were
   * dropped, leaving an unstitched sliver at each end of the shape.
   */
  const extend = (arc: Point[]): Point[] => {
    const out = [...arc];
    const head = out[0];
    const headShift = tMin - project(head);
    if (headShift < 0) out.unshift({ x: head.x + ux * headShift, y: head.y + uy * headShift });
    const tail = out[out.length - 1];
    const tailShift = tMax - project(tail);
    if (tailShift > 0) out.push({ x: tail.x + ux * tailShift, y: tail.y + uy * tailShift });
    return out;
  };

  return { left: extend(orient(arcs[0])), right: extend(orient(arcs[1])) };
}

function columnFromFarthestPair(ring: Ring): SatinColumn | null {
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
