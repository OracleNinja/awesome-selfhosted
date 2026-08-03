/**
 * Underlay generation.
 *
 * Underlay stabilises the fabric and gives the top stitching something to sit
 * on. Without it fills sink into knit fabrics and satin columns wander.
 */

import { insetRing, outsetRing, type Point, type Polygon } from '../../domain/geometry';
import type { UnderlaySettings } from '../../domain/embroidery-object';
import { generateFill } from './fill';
import { runningStitch } from './running';
import { columnCenterline, type SatinColumn } from './satin';

export function polygonUnderlay(
  poly: Polygon,
  settings: UnderlaySettings,
  fillAngle: number,
  minStitchLength: number,
): Point[][] {
  if (settings.type === 'none') return [];
  const runs: Point[][] = [];

  if (settings.type === 'edge-run' || settings.type === 'full') {
    const ring = insetRing(poly.outer, settings.inset);
    if (ring.length >= 3) {
      runs.push(runningStitch([...ring, ring[0]], { stitchLength: settings.stitchLength, minStitchLength }));
    }
    for (const hole of poly.holes) {
      // A hole is offset away from its own interior so the underlay run lands
      // in the material rather than inside the hole.
      const h = outsetRing(hole, settings.inset);
      if (h.length >= 3) {
        runs.push(runningStitch([...h, h[0]], { stitchLength: settings.stitchLength, minStitchLength }));
      }
    }
  }

  if (settings.type === 'zigzag' || settings.type === 'full') {
    // A widely-spaced fill running across the top fill's direction.
    const inner: Polygon = {
      outer: insetRing(poly.outer, settings.inset),
      holes: poly.holes,
    };
    if (inner.outer.length >= 3) {
      runs.push(
        ...generateFill(inner, {
          angle: fillAngle + 90,
          density: settings.spacing,
          stitchLength: settings.stitchLength,
          minStitchLength,
          stagger: 0,
          maxTravelStitch: 100,
        }),
      );
    }
  }

  if (settings.type === 'center-run') {
    // Not meaningful for an arbitrary polygon; the caller uses satinUnderlay.
    return runs;
  }

  return runs.filter((r) => r.length >= 2);
}

export function satinUnderlay(
  column: SatinColumn,
  settings: UnderlaySettings,
  minStitchLength: number,
): Point[][] {
  if (settings.type === 'none') return [];
  const runs: Point[][] = [];

  if (settings.type === 'center-run' || settings.type === 'full' || settings.type === 'edge-run') {
    const center = columnCenterline(column);
    if (center.length >= 2) {
      runs.push(runningStitch(center, { stitchLength: settings.stitchLength, minStitchLength }));
    }
  }

  if (settings.type === 'zigzag' || settings.type === 'full') {
    // A coarse zigzag inside the column edges.
    const inset = 0.25;
    const left = column.left.map((p, i) => {
      const r = column.right[Math.min(i, column.right.length - 1)];
      return { x: p.x + (r.x - p.x) * inset, y: p.y + (r.y - p.y) * inset };
    });
    const right = column.right.map((p, i) => {
      const l = column.left[Math.min(i, column.left.length - 1)];
      return { x: p.x + (l.x - p.x) * inset, y: p.y + (l.y - p.y) * inset };
    });
    const coarse: Point[] = [];
    const n = Math.max(2, Math.min(left.length, right.length));
    for (let i = 0; i < n; i++) coarse.push(i % 2 === 0 ? left[i] : right[i]);
    if (coarse.length >= 2) runs.push(coarse);
  }

  return runs.filter((r) => r.length >= 2);
}
