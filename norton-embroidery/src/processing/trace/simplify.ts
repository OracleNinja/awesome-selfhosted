/** Polyline simplification and smoothing for traced outlines. */

import { distance, type Point, type Polygon, type Ring } from '../../domain/geometry';

/** Ramer-Douglas-Peucker simplification of an open polyline. */
export function simplifyPath(points: readonly Point[], tolerance: number): Point[] {
  if (points.length <= 2) return [...points];
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;

  const stack: Array<[number, number]> = [[0, points.length - 1]];
  while (stack.length) {
    const [first, last] = stack.pop()!;
    if (last <= first + 1) continue;
    let maxDist = 0;
    let index = -1;
    for (let i = first + 1; i < last; i++) {
      const d = perpendicularDistance(points[i], points[first], points[last]);
      if (d > maxDist) {
        maxDist = d;
        index = i;
      }
    }
    if (index >= 0 && maxDist > tolerance) {
      keep[index] = 1;
      stack.push([first, index], [index, last]);
    }
  }

  const out: Point[] = [];
  for (let i = 0; i < points.length; i++) if (keep[i]) out.push(points[i]);
  return out;
}

/** Simplify a closed ring, keeping it closed and at least a triangle. */
export function simplifyRing(ring: Ring, tolerance: number): Ring {
  if (ring.length <= 3) return [...ring];
  // Anchor at the point farthest from the centroid so the split does not fall
  // in the middle of a smooth run.
  let cx = 0;
  let cy = 0;
  for (const p of ring) {
    cx += p.x;
    cy += p.y;
  }
  cx /= ring.length;
  cy /= ring.length;
  let anchor = 0;
  let best = -1;
  for (let i = 0; i < ring.length; i++) {
    const d = (ring[i].x - cx) ** 2 + (ring[i].y - cy) ** 2;
    if (d > best) {
      best = d;
      anchor = i;
    }
  }
  const rotated = [...ring.slice(anchor), ...ring.slice(0, anchor)];
  const open = [...rotated, rotated[0]];
  const simplified = simplifyPath(open, tolerance);
  simplified.pop();
  return simplified.length >= 3 ? simplified : ring;
}

function perpendicularDistance(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return distance(p, a);
  return Math.abs((p.x - a.x) * dy - (p.y - a.y) * dx) / len;
}

/**
 * Chaikin corner cutting, applied to a closed ring. Two passes turn the
 * staircase of a traced bitmap outline into a smooth outline without moving it
 * more than half a pixel.
 */
export function smoothRing(ring: Ring, passes = 2): Ring {
  let cur = ring;
  for (let p = 0; p < passes; p++) {
    if (cur.length < 4) break;
    const next: Ring = [];
    for (let i = 0; i < cur.length; i++) {
      const a = cur[i];
      const b = cur[(i + 1) % cur.length];
      next.push({ x: a.x * 0.75 + b.x * 0.25, y: a.y * 0.75 + b.y * 0.25 });
      next.push({ x: a.x * 0.25 + b.x * 0.75, y: a.y * 0.25 + b.y * 0.75 });
    }
    cur = next;
  }
  return cur;
}

export function smoothPath(path: Point[], passes = 2): Point[] {
  let cur = path;
  for (let p = 0; p < passes; p++) {
    if (cur.length < 3) break;
    const next: Point[] = [cur[0]];
    for (let i = 0; i < cur.length - 1; i++) {
      const a = cur[i];
      const b = cur[i + 1];
      next.push({ x: a.x * 0.75 + b.x * 0.25, y: a.y * 0.75 + b.y * 0.25 });
      next.push({ x: a.x * 0.25 + b.x * 0.75, y: a.y * 0.25 + b.y * 0.75 });
    }
    next.push(cur[cur.length - 1]);
    cur = next;
  }
  return cur;
}

export function simplifyPolygon(poly: Polygon, tolerance: number): Polygon {
  return {
    outer: simplifyRing(poly.outer, tolerance),
    holes: poly.holes.map((h) => simplifyRing(h, tolerance)).filter((h) => h.length >= 3),
  };
}

export function smoothPolygon(poly: Polygon, passes = 2): Polygon {
  return {
    outer: smoothRing(poly.outer, passes),
    holes: poly.holes.map((h) => smoothRing(h, passes)),
  };
}

export function transformPolygon(poly: Polygon, fn: (p: Point) => Point): Polygon {
  return { outer: poly.outer.map(fn), holes: poly.holes.map((h) => h.map(fn)) };
}
