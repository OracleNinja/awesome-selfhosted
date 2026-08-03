/**
 * Tatami fill generation.
 *
 * Rows of stitching are laid at the requested angle across the polygon. Rows
 * are grouped into vertically-connected components so the needle never crosses
 * a hole; each component is sewn as one serpentine run. Where two runs can be
 * joined without leaving the shape, they are joined with ordinary stitches
 * instead of a jump.
 */

import {
  degToRad,
  densifyPath,
  distance,
  insetRing,
  pointInPolygon,
  rotatePoint,
  type Point,
  type Polygon,
} from '../../domain/geometry';
import { dropShortSegments } from './running';

export interface FillOptions {
  /** Row angle in degrees. 0 = rows run horizontally. */
  angle: number;
  /** Distance between rows, 0.1 mm units. */
  density: number;
  /** Maximum stitch length along a row, 0.1 mm units. */
  stitchLength: number;
  minStitchLength: number;
  /** Number of rows across which the stitch split points rotate. */
  stagger: number;
  /** Longest connection sewn as stitches rather than a jump, 0.1 mm units. */
  maxTravelStitch: number;
}

export const DEFAULT_FILL_OPTIONS: FillOptions = {
  angle: 0,
  density: 4,
  stitchLength: 35,
  minStitchLength: 6,
  stagger: 4,
  maxTravelStitch: 100,
};

interface Span {
  row: number;
  x0: number;
  x1: number;
  y: number;
  component: number;
}

/**
 * Generate the fill. Returns one or more continuous runs; the caller decides
 * how to travel between runs (jump / trim).
 */
export function generateFill(poly: Polygon, options: FillOptions): Point[][] {
  if (poly.outer.length < 3 || options.density <= 0) return [];

  const angleRad = degToRad(options.angle);
  const origin = centroid(poly.outer);
  const toLocal = (p: Point): Point => rotatePoint(p, -angleRad, origin);
  const toWorld = (p: Point): Point => rotatePoint(p, angleRad, origin);

  const rings = [poly.outer, ...poly.holes].map((r) => r.map(toLocal));

  let minY = Infinity;
  let maxY = -Infinity;
  for (const ring of rings) {
    for (const p of ring) {
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
  }
  if (!Number.isFinite(minY) || maxY - minY < options.density * 0.5) {
    // Too thin for even one row: stitch the outline instead of nothing.
    return [];
  }

  // --- scan rows --------------------------------------------------------
  const spans: Span[] = [];
  let row = 0;
  // Start half a row in so the first row is not exactly on the boundary.
  for (let y = minY + options.density / 2; y <= maxY; y += options.density, row++) {
    const xs = scanlineIntersections(rings, y);
    for (let i = 0; i + 1 < xs.length; i += 2) {
      const x0 = xs[i];
      const x1 = xs[i + 1];
      if (x1 - x0 < 1e-6) continue;
      spans.push({ row, x0, x1, y, component: -1 });
    }
  }
  if (spans.length === 0) return [];

  assignComponents(spans);

  // --- serpentine per component ----------------------------------------
  const byComponent = new Map<number, Span[]>();
  for (const s of spans) {
    const list = byComponent.get(s.component);
    if (list) list.push(s);
    else byComponent.set(s.component, [s]);
  }

  const insetOuter = insetRing(poly.outer, Math.min(8, options.density * 2)).map(toLocal);
  const runs: Point[][] = [];

  for (const group of byComponent.values()) {
    group.sort((a, b) => a.row - b.row || a.x0 - b.x0);
    let current: Point[] = [];
    let leftToRight = true;
    let lastRow = -1;

    for (const span of group) {
      if (span.row !== lastRow) {
        leftToRight = !leftToRight;
        lastRow = span.row;
      }
      const rowPoints = rowStitches(span, options, leftToRight);
      if (rowPoints.length === 0) continue;

      if (current.length === 0) {
        current = rowPoints;
        continue;
      }
      const from = current[current.length - 1];
      const to = rowPoints[0];
      const gap = distance(from, to);

      if (gap <= options.maxTravelStitch && segmentInside(from, to, rings)) {
        current.push(...rowPoints);
      } else {
        const detour = travelAlongRing(insetOuter, from, to, options);
        if (detour && pathLength(detour) <= options.maxTravelStitch * 6) {
          current.push(...detour, ...rowPoints);
        } else {
          runs.push(current);
          current = rowPoints;
        }
      }
    }
    if (current.length) runs.push(current);
  }

  return runs
    .map((run) => dropShortSegments(run.map(toWorld), options.minStitchLength))
    .filter((run) => run.length >= 2);
}

function rowStitches(span: Span, options: FillOptions, leftToRight: boolean): Point[] {
  const length = span.x1 - span.x0;
  if (length <= 0) return [];
  const steps = Math.max(1, Math.ceil(length / options.stitchLength));
  // Rotate the split points row by row so the joins do not line up.
  const phase = options.stagger > 0 ? (span.row % options.stagger) / options.stagger : 0;
  const offset = phase * (length / steps);

  const xs: number[] = [span.x0];
  for (let i = 1; i < steps; i++) {
    const x = span.x0 + offset + (i * length) / steps;
    if (x > span.x0 && x < span.x1) xs.push(x);
  }
  xs.push(span.x1);

  const points = xs.map((x) => ({ x, y: span.y }));
  return leftToRight ? points : points.reverse();
}

/** Sorted x positions where the horizontal line y crosses the rings. */
function scanlineIntersections(rings: Point[][], y: number): number[] {
  const xs: number[] = [];
  for (const ring of rings) {
    const n = ring.length;
    for (let i = 0; i < n; i++) {
      const a = ring[i];
      const b = ring[(i + 1) % n];
      // Half-open rule avoids double counting at shared vertices.
      if (a.y <= y === b.y <= y) continue;
      const t = (y - a.y) / (b.y - a.y);
      xs.push(a.x + t * (b.x - a.x));
    }
  }
  xs.sort((p, q) => p - q);
  return xs;
}

/** Union spans that overlap horizontally on adjacent rows. */
function assignComponents(spans: Span[]): void {
  const parent = spans.map((_, i) => i);
  const find = (i: number): number => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]];
      i = parent[i];
    }
    return i;
  };
  const union = (a: number, b: number): void => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent[rb] = ra;
  };

  const byRow = new Map<number, number[]>();
  spans.forEach((s, i) => {
    const list = byRow.get(s.row);
    if (list) list.push(i);
    else byRow.set(s.row, [i]);
  });

  for (const [rowIndex, indices] of byRow) {
    const next = byRow.get(rowIndex + 1);
    if (!next) continue;
    for (const i of indices) {
      for (const j of next) {
        if (spans[i].x0 < spans[j].x1 && spans[j].x0 < spans[i].x1) union(i, j);
      }
    }
  }

  spans.forEach((s, i) => {
    s.component = find(i);
  });
}

/** True when the straight segment a->b stays inside the shape. */
function segmentInside(a: Point, b: Point, rings: Point[][]): boolean {
  const poly: Polygon = { outer: rings[0], holes: rings.slice(1) };
  const steps = Math.max(2, Math.ceil(distance(a, b) / 5));
  for (let i = 1; i < steps; i++) {
    const t = i / steps;
    const p = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
    if (!pointInPolygon(p, poly)) return false;
  }
  return true;
}

/**
 * Travel from `from` to `to` by following an inset copy of the outer ring, so
 * the connecting stitches stay hidden under the fill instead of jumping.
 *
 * The ring is densified first: a traced square has only four vertices, and
 * hopping straight to the nearest one would emit a stitch far longer than the
 * machine allows.
 */
function travelAlongRing(
  ring: Point[],
  from: Point,
  to: Point,
  options: FillOptions,
): Point[] | null {
  if (ring.length < 3) return null;

  // Close the ring, densify, then drop the duplicated closing point.
  const dense = densifyPath([...ring, ring[0]], options.stitchLength);
  dense.pop();
  if (dense.length < 3) return null;

  const start = nearestIndex(dense, from);
  const end = nearestIndex(dense, to);
  if (start === end) return null;

  const forward: Point[] = [];
  for (let i = start; i !== end; i = (i + 1) % dense.length) forward.push(dense[i]);
  forward.push(dense[end]);

  const backward: Point[] = [];
  for (let i = start; i !== end; i = (i - 1 + dense.length) % dense.length) backward.push(dense[i]);
  backward.push(dense[end]);

  const arc = pathLength(forward) <= pathLength(backward) ? forward : backward;

  // Include the hops onto and off the ring, and split them like any other
  // stitch so nothing exceeds the stitch length. The caller is already at
  // `from` and appends the row starting at `to`, so both ends are dropped.
  const full = densifyPath([from, ...arc, to], options.stitchLength);
  return full.slice(1, -1);
}

function nearestIndex(ring: Point[], p: Point): number {
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < ring.length; i++) {
    const d = distance(ring[i], p);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

function pathLength(points: Point[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += distance(points[i - 1], points[i]);
  return total;
}

function centroid(ring: readonly Point[]): Point {
  let x = 0;
  let y = 0;
  for (const p of ring) {
    x += p.x;
    y += p.y;
  }
  return { x: x / ring.length, y: y / ring.length };
}
