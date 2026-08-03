/**
 * Contour tracing.
 *
 * Boundaries are followed along the "cracks" between foreground and background
 * pixels rather than through pixel centres. That guarantees closed, non
 * self-intersecting rings with an exact winding direction, which is what the
 * fill and satin generators need. The resulting staircase outline is smoothed
 * afterwards by `simplify.ts`.
 *
 * Coordinates are lattice coordinates: pixel (i, j) occupies the unit square
 * with corners (i, j) .. (i+1, j+1).
 */

import { signedArea, pointInRing, ringArea, type Point, type Polygon, type Ring } from '../../domain/geometry';

interface Edge {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
}

const key = (x: number, y: number): number => y * 100000 + x;

/**
 * Trace all boundary rings of a binary mask. Outer rings wind clockwise
 * (positive signed area in the y-down system); holes wind counter-clockwise.
 */
export function traceRings(mask: Uint8Array, w: number, h: number): Ring[] {
  const at = (x: number, y: number): number => (x < 0 || y < 0 || x >= w || y >= h ? 0 : mask[y * w + x]);

  // Collect oriented boundary edges (foreground kept on the right of travel).
  const outgoing = new Map<number, Edge[]>();
  const addEdge = (fromX: number, fromY: number, toX: number, toY: number): void => {
    const k = key(fromX, fromY);
    const list = outgoing.get(k);
    const edge = { fromX, fromY, toX, toY };
    if (list) list.push(edge);
    else outgoing.set(k, [edge]);
  };

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (!at(x, y)) continue;
      if (!at(x, y - 1)) addEdge(x, y, x + 1, y); // top, travelling right
      if (!at(x + 1, y)) addEdge(x + 1, y, x + 1, y + 1); // right, travelling down
      if (!at(x, y + 1)) addEdge(x + 1, y + 1, x, y + 1); // bottom, travelling left
      if (!at(x - 1, y)) addEdge(x, y + 1, x, y); // left, travelling up
    }
  }

  const rings: Ring[] = [];
  const used = new Set<Edge>();

  for (const [, edges] of outgoing) {
    for (const startEdge of edges) {
      if (used.has(startEdge)) continue;
      const ring: Ring = [];
      let edge: Edge | undefined = startEdge;
      while (edge && !used.has(edge)) {
        used.add(edge);
        ring.push({ x: edge.fromX, y: edge.fromY });
        edge = nextEdge(outgoing, used, edge);
      }
      if (ring.length >= 4) rings.push(collapseCollinear(ring));
    }
  }

  return rings;
}

/**
 * Pick the continuation at a lattice point. When several edges leave the same
 * point (a diagonal pinch in the mask) the sharpest right turn is taken, which
 * keeps the two lobes of the pinch in separate loops.
 */
function nextEdge(
  outgoing: Map<number, Edge[]>,
  used: Set<Edge>,
  current: Edge,
): Edge | undefined {
  const candidates = outgoing.get(key(current.toX, current.toY));
  if (!candidates) return undefined;
  const free = candidates.filter((e) => !used.has(e));
  if (free.length === 0) return undefined;
  if (free.length === 1) return free[0];

  const inDx = current.toX - current.fromX;
  const inDy = current.toY - current.fromY;
  let best = free[0];
  let bestScore = -Infinity;
  for (const e of free) {
    const dx = e.toX - e.fromX;
    const dy = e.toY - e.fromY;
    // cross < 0 is a right turn in a y-down system; rank right turns first.
    const cross = inDx * dy - inDy * dx;
    const dot = inDx * dx + inDy * dy;
    const score = cross < 0 ? 3 : cross === 0 && dot > 0 ? 2 : cross === 0 ? 0 : 1;
    if (score > bestScore) {
      bestScore = score;
      best = e;
    }
  }
  return best;
}

function collapseCollinear(ring: Ring): Ring {
  const out: Ring = [];
  const n = ring.length;
  for (let i = 0; i < n; i++) {
    const prev = ring[(i - 1 + n) % n];
    const cur = ring[i];
    const next = ring[(i + 1) % n];
    const cross = (cur.x - prev.x) * (next.y - cur.y) - (cur.y - prev.y) * (next.x - cur.x);
    if (cross !== 0) out.push(cur);
  }
  return out.length >= 3 ? out : ring;
}

/**
 * Build polygons (outer + holes) from a mask. A mask holding several
 * disconnected blobs yields several polygons.
 */
export function maskToPolygons(mask: Uint8Array, w: number, h: number): Polygon[] {
  const rings = traceRings(mask, w, h);
  const outers = rings.filter((r) => signedArea(r) > 0);
  const holes = rings.filter((r) => signedArea(r) < 0);

  const polygons: Polygon[] = outers
    .sort((a, b) => ringArea(b) - ringArea(a))
    .map((outer) => ({ outer, holes: [] as Ring[] }));

  for (const hole of holes) {
    const probe = ringCentroidInside(hole);
    // Assign to the smallest outer ring that contains the hole.
    let target: Polygon | undefined;
    let targetArea = Infinity;
    for (const poly of polygons) {
      if (!pointInRing(probe, poly.outer)) continue;
      const a = ringArea(poly.outer);
      if (a < targetArea) {
        targetArea = a;
        target = poly;
      }
    }
    if (target) target.holes.push(hole);
  }

  return polygons.filter((p) => p.outer.length >= 3);
}

/** A point guaranteed to lie inside the ring (used to test hole containment). */
function ringCentroidInside(ring: Ring): Point {
  let cx = 0;
  let cy = 0;
  for (const p of ring) {
    cx += p.x;
    cy += p.y;
  }
  cx /= ring.length;
  cy /= ring.length;
  const centroid = { x: cx, y: cy };
  if (pointInRing(centroid, ring)) return centroid;
  // Concave ring: fall back to a horizontal ray sample at a vertex height.
  for (let i = 0; i < ring.length; i++) {
    const y = (ring[i].y + ring[(i + 1) % ring.length].y) / 2;
    const xs: number[] = [];
    for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
      const a = ring[j];
      const b = ring[k];
      if (a.y > y !== b.y > y) xs.push(a.x + ((y - a.y) / (b.y - a.y)) * (b.x - a.x));
    }
    xs.sort((p, q) => p - q);
    if (xs.length >= 2) return { x: (xs[0] + xs[1]) / 2, y };
  }
  return centroid;
}
