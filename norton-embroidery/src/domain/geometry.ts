/** Basic 2D geometry helpers used across tracing, digitizing and stitching. */

export interface Point {
  x: number;
  y: number;
}

/** A closed ring of points (first point is NOT repeated at the end). */
export type Ring = Point[];

/** A polygon: one outer ring plus zero or more hole rings. */
export interface Polygon {
  outer: Ring;
  holes: Ring[];
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export const EMPTY_BOUNDS: Bounds = {
  minX: Infinity,
  minY: Infinity,
  maxX: -Infinity,
  maxY: -Infinity,
};

export function boundsWidth(b: Bounds): number {
  return Number.isFinite(b.maxX) && Number.isFinite(b.minX) ? b.maxX - b.minX : 0;
}

export function boundsHeight(b: Bounds): number {
  return Number.isFinite(b.maxY) && Number.isFinite(b.minY) ? b.maxY - b.minY : 0;
}

export function boundsOf(points: readonly Point[]): Bounds {
  const b: Bounds = { ...EMPTY_BOUNDS };
  for (const p of points) {
    if (p.x < b.minX) b.minX = p.x;
    if (p.y < b.minY) b.minY = p.y;
    if (p.x > b.maxX) b.maxX = p.x;
    if (p.y > b.maxY) b.maxY = p.y;
  }
  return b;
}

export function mergeBounds(a: Bounds, b: Bounds): Bounds {
  return {
    minX: Math.min(a.minX, b.minX),
    minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX),
    maxY: Math.max(a.maxY, b.maxY),
  };
}

export function boundsCenter(b: Bounds): Point {
  return { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 };
}

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

export function distanceSq(a: Point, b: Point): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return dx * dx + dy * dy;
}

export function lerp(a: Point, b: Point, t: number): Point {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
}

/** Signed area of a ring. Positive == clockwise in a y-down coordinate system. */
export function signedArea(ring: readonly Point[]): number {
  let sum = 0;
  const n = ring.length;
  for (let i = 0; i < n; i++) {
    const a = ring[i];
    const b = ring[(i + 1) % n];
    sum += a.x * b.y - b.x * a.y;
  }
  return sum / 2;
}

export function ringArea(ring: readonly Point[]): number {
  return Math.abs(signedArea(ring));
}

export function polygonArea(poly: Polygon): number {
  let area = ringArea(poly.outer);
  for (const h of poly.holes) area -= ringArea(h);
  return Math.max(0, area);
}

export function ringPerimeter(ring: readonly Point[]): number {
  let total = 0;
  const n = ring.length;
  for (let i = 0; i < n; i++) total += distance(ring[i], ring[(i + 1) % n]);
  return total;
}

export function pathLength(path: readonly Point[]): number {
  let total = 0;
  for (let i = 1; i < path.length; i++) total += distance(path[i - 1], path[i]);
  return total;
}

/** Ensure a ring winds in the requested direction (clockwise in y-down space). */
export function orientRing(ring: Ring, clockwise: boolean): Ring {
  const isCw = signedArea(ring) > 0;
  return isCw === clockwise ? ring : [...ring].reverse();
}

export function pointInRing(p: Point, ring: readonly Point[]): boolean {
  let inside = false;
  const n = ring.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const a = ring[i];
    const b = ring[j];
    const intersects = a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x;
    if (intersects) inside = !inside;
  }
  return inside;
}

export function pointInPolygon(p: Point, poly: Polygon): boolean {
  if (!pointInRing(p, poly.outer)) return false;
  for (const h of poly.holes) if (pointInRing(p, h)) return false;
  return true;
}

export function rotatePoint(p: Point, angleRad: number, origin: Point = { x: 0, y: 0 }): Point {
  const cos = Math.cos(angleRad);
  const sin = Math.sin(angleRad);
  const dx = p.x - origin.x;
  const dy = p.y - origin.y;
  return {
    x: origin.x + dx * cos - dy * sin,
    y: origin.y + dx * sin + dy * cos,
  };
}

export function rotatePoints(points: readonly Point[], angleRad: number, origin: Point): Point[] {
  return points.map((p) => rotatePoint(p, angleRad, origin));
}

export function translatePoints(points: readonly Point[], dx: number, dy: number): Point[] {
  return points.map((p) => ({ x: p.x + dx, y: p.y + dy }));
}

export function scalePoints(points: readonly Point[], sx: number, sy: number, origin: Point): Point[] {
  return points.map((p) => ({
    x: origin.x + (p.x - origin.x) * sx,
    y: origin.y + (p.y - origin.y) * sy,
  }));
}

/** Resample a polyline so consecutive points are at most `maxSpacing` apart. */
export function densifyPath(path: readonly Point[], maxSpacing: number): Point[] {
  if (path.length < 2 || maxSpacing <= 0) return [...path];
  const out: Point[] = [path[0]];
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1];
    const b = path[i];
    const d = distance(a, b);
    const steps = Math.max(1, Math.ceil(d / maxSpacing));
    for (let s = 1; s <= steps; s++) out.push(lerp(a, b, s / steps));
  }
  return out;
}

/** Resample a polyline into exactly `count` evenly spaced points (by arc length). */
export function resamplePath(path: readonly Point[], count: number): Point[] {
  if (path.length === 0) return [];
  if (count <= 1) return [path[0]];
  if (path.length === 1) return new Array(count).fill(null).map(() => ({ ...path[0] }));

  const cum: number[] = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + distance(path[i - 1], path[i]));
  const total = cum[cum.length - 1];
  if (total === 0) return new Array(count).fill(null).map(() => ({ ...path[0] }));

  const out: Point[] = [];
  let seg = 1;
  for (let i = 0; i < count; i++) {
    const target = (total * i) / (count - 1);
    while (seg < cum.length - 1 && cum[seg] < target) seg++;
    const segStart = cum[seg - 1];
    const segLen = cum[seg] - segStart;
    const t = segLen === 0 ? 0 : (target - segStart) / segLen;
    out.push(lerp(path[seg - 1], path[seg], t));
  }
  return out;
}

/** Outward-ish normal of a polyline at index i (unit length). */
export function pathNormal(path: readonly Point[], i: number): Point {
  const a = path[Math.max(0, i - 1)];
  const b = path[Math.min(path.length - 1, i + 1)];
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return { x: -dy / len, y: dx / len };
}

/**
 * Offset a closed ring by `dist` using a vertex-normal shift.
 *
 * This is not a full polygon-offset implementation; it is used for underlay and
 * for hidden travel paths, where small inaccuracies at concave corners are
 * acceptable. Vertices that end up on the wrong side of the original ring
 * (which happens on sharp corners when `dist` is large) are dropped rather than
 * producing a self-intersecting result.
 *
 * `mode: 'inward'` moves toward the ring's interior; `'outward'` moves away
 * from it, which is what a hole needs so the offset lands in the material.
 */
export function offsetRing(ring: Ring, dist: number, mode: 'inward' | 'outward' = 'inward'): Ring {
  const n = ring.length;
  if (n < 3) return [...ring];
  // In a y-down system the right-hand normal points out of a clockwise ring.
  const cw = signedArea(ring) > 0;
  const sign = (cw ? -1 : 1) * (mode === 'inward' ? 1 : -1);
  const wantInside = mode === 'inward';

  const out: Ring = [];
  for (let i = 0; i < n; i++) {
    const prev = ring[(i - 1 + n) % n];
    const cur = ring[i];
    const next = ring[(i + 1) % n];
    const n1 = normalOf(prev, cur);
    const n2 = normalOf(cur, next);
    let nx = (n1.x + n2.x) / 2;
    let ny = (n1.y + n2.y) / 2;
    const len = Math.hypot(nx, ny);
    if (len < 1e-9) continue;
    nx /= len;
    ny /= len;
    const candidate = { x: cur.x + sign * nx * dist, y: cur.y + sign * ny * dist };
    if (pointInRing(candidate, ring) === wantInside) out.push(candidate);
  }
  return out.length >= 3 ? out : [];
}

/** Shrink a ring toward its interior. */
export function insetRing(ring: Ring, dist: number): Ring {
  return offsetRing(ring, dist, 'inward');
}

/** Grow a ring away from its interior. Used to push hole edges into material. */
export function outsetRing(ring: Ring, dist: number): Ring {
  return offsetRing(ring, dist, 'outward');
}

function normalOf(a: Point, b: Point): Point {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return { x: dy / len, y: -dx / len };
}

export const degToRad = (deg: number): number => (deg * Math.PI) / 180;
export const radToDeg = (rad: number): number => (rad * 180) / Math.PI;
