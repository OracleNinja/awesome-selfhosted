/**
 * Sew-order optimisation.
 *
 * Three competing goals are balanced here:
 *   1. visual correctness — larger background shapes must sew before the
 *      details that sit on top of them, or the detail disappears;
 *   2. thread changes — objects sharing a colour should sew together;
 *   3. travel — within a colour, sew nearby objects consecutively.
 *
 * Layering wins over colour grouping: an object that overlaps another and sews
 * on top of it keeps its position even when that costs an extra colour change.
 */

import { boundsOf, distance, type Bounds, type Point } from '../../domain/geometry';
import { objectPoints, type EmbroideryObject } from '../../domain/embroidery-object';

function areaOf(b: Bounds): number {
  return Math.max(0, b.maxX - b.minX) * Math.max(0, b.maxY - b.minY);
}

function contains(outer: Bounds, inner: Bounds): boolean {
  return (
    outer.minX <= inner.minX &&
    outer.minY <= inner.minY &&
    outer.maxX >= inner.maxX &&
    outer.maxY >= inner.maxY
  );
}

export interface OrderResult {
  objects: EmbroideryObject[];
  /** Colour changes the resulting order requires. */
  colorChanges: number;
  /** Total distance between the end of one object and the start of the next. */
  travelDistance: number;
}

/**
 * Produce a sew order. Objects are first split into layers: an object fully
 * inside a larger object must sew after it. Within a layer, objects are grouped
 * by thread and ordered nearest-neighbour.
 */
export function optimizeOrder(objects: readonly EmbroideryObject[]): OrderResult {
  if (objects.length === 0) return { objects: [], colorChanges: 0, travelDistance: 0 };

  const withBounds = objects.map((obj) => ({ obj, bounds: boundsOf(objectPoints(obj)) }));

  // Layer 0 = nothing contains it; layer n = contained by something in layer n-1.
  const layerOf = new Map<string, number>();
  const computeLayer = (index: number, seen: Set<string>): number => {
    const { obj, bounds } = withBounds[index];
    const cached = layerOf.get(obj.id);
    if (cached !== undefined) return cached;
    if (seen.has(obj.id)) return 0;
    seen.add(obj.id);

    let layer = 0;
    for (let j = 0; j < withBounds.length; j++) {
      if (j === index) continue;
      const other = withBounds[j];
      if (areaOf(other.bounds) <= areaOf(bounds)) continue;
      if (!contains(other.bounds, bounds)) continue;
      layer = Math.max(layer, computeLayer(j, seen) + 1);
    }
    layerOf.set(obj.id, layer);
    return layer;
  };
  withBounds.forEach((_, i) => computeLayer(i, new Set()));

  const layers = new Map<number, typeof withBounds>();
  for (const entry of withBounds) {
    const layer = layerOf.get(entry.obj.id) ?? 0;
    const list = layers.get(layer);
    if (list) list.push(entry);
    else layers.set(layer, [entry]);
  }

  const result: EmbroideryObject[] = [];
  let cursor: Point = { x: 0, y: 0 };
  let travel = 0;
  let colorChanges = 0;
  let lastThread: number | null = null;

  for (const layer of [...layers.keys()].sort((a, b) => a - b)) {
    const entries = layers.get(layer)!;

    // Group by thread, largest group first so a big colour block sews together.
    const byThread = new Map<number, typeof entries>();
    for (const e of entries) {
      const list = byThread.get(e.obj.threadIndex);
      if (list) list.push(e);
      else byThread.set(e.obj.threadIndex, [e]);
    }

    // Start with the thread already on the machine when possible.
    const threads = [...byThread.keys()].sort((a, b) => {
      if (a === lastThread) return -1;
      if (b === lastThread) return 1;
      return (byThread.get(b)!.length - byThread.get(a)!.length) || a - b;
    });

    for (const thread of threads) {
      if (lastThread !== null && thread !== lastThread) colorChanges++;
      lastThread = thread;

      // Nearest-neighbour within the colour, biggest object first.
      const pending = [...byThread.get(thread)!].sort(
        (a, b) => areaOf(b.bounds) - areaOf(a.bounds),
      );
      while (pending.length) {
        let bestIndex = 0;
        let bestDist = Infinity;
        for (let i = 0; i < pending.length; i++) {
          const p = startPointOf(pending[i].obj);
          const d = distance(cursor, p);
          if (d < bestDist) {
            bestDist = d;
            bestIndex = i;
          }
        }
        const [chosen] = pending.splice(bestIndex, 1);
        travel += bestDist;
        cursor = endPointOf(chosen.obj);
        result.push(chosen.obj);
      }
    }
  }

  const ordered = result.map((obj, i) => ({ ...obj, order: i }));
  return { objects: ordered, colorChanges, travelDistance: travel };
}

function startPointOf(obj: EmbroideryObject): Point {
  const pts = objectPoints(obj);
  return pts[0] ?? { x: 0, y: 0 };
}

function endPointOf(obj: EmbroideryObject): Point {
  const pts = objectPoints(obj);
  return pts[pts.length - 1] ?? { x: 0, y: 0 };
}

/** Recompute `order` after a manual reorder in the editor. */
export function renumber(objects: readonly EmbroideryObject[]): EmbroideryObject[] {
  return [...objects]
    .sort((a, b) => a.order - b.order)
    .map((obj, i) => ({ ...obj, order: i }));
}

/** Move an object to a new index in the sew order. */
export function moveInOrder(
  objects: readonly EmbroideryObject[],
  objectId: string,
  newIndex: number,
): EmbroideryObject[] {
  const sorted = [...objects].sort((a, b) => a.order - b.order);
  const from = sorted.findIndex((o) => o.id === objectId);
  if (from < 0) return sorted;
  const clamped = Math.max(0, Math.min(sorted.length - 1, newIndex));
  const [moved] = sorted.splice(from, 1);
  sorted.splice(clamped, 0, moved);
  return sorted.map((obj, i) => ({ ...obj, order: i }));
}
