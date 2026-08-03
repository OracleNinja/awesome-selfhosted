/** Running, bean and outline stitch generation. */

import { densifyPath, distance, type Point, type Ring } from '../../domain/geometry';

export interface RunningOptions {
  stitchLength: number;
  /** Drop points closer together than this (0.1 mm units). */
  minStitchLength: number;
}

/** Even-spaced needle penetrations along a polyline. */
export function runningStitch(path: readonly Point[], options: RunningOptions): Point[] {
  if (path.length < 2) return path.length ? [{ ...path[0] }] : [];
  const dense = densifyPath(path, options.stitchLength);
  return dropShortSegments(dense, options.minStitchLength);
}

/** Running stitch around a closed ring, returning to the start point. */
export function outlineStitch(ring: Ring, options: RunningOptions): Point[] {
  if (ring.length < 2) return [];
  return runningStitch([...ring, ring[0]], options);
}

/**
 * Bean stitch: the same path sewn back and forth `repeats` times, giving a
 * heavier line. `repeats` must be odd so the needle finishes at the far end.
 */
export function beanStitch(
  path: readonly Point[],
  repeats: number,
  options: RunningOptions,
): Point[] {
  const base = runningStitch(path, options);
  if (base.length < 2) return base;
  const passes = Math.max(1, repeats % 2 === 0 ? repeats + 1 : repeats);
  const out: Point[] = [...base];
  for (let p = 1; p < passes; p++) {
    const source = p % 2 === 1 ? [...base].reverse() : base;
    // Skip the first point of each pass: the needle is already there.
    for (let i = 1; i < source.length; i++) out.push({ ...source[i] });
  }
  return out;
}

/**
 * Remove intermediate points that sit closer than `minLength` to the previous
 * kept point. Very short stitches cause thread breaks and needle damage.
 * Endpoints are always preserved.
 */
export function dropShortSegments(points: readonly Point[], minLength: number): Point[] {
  if (points.length <= 2 || minLength <= 0) return [...points];
  const out: Point[] = [points[0]];
  for (let i = 1; i < points.length - 1; i++) {
    if (distance(out[out.length - 1], points[i]) >= minLength) out.push(points[i]);
  }
  const last = points[points.length - 1];
  if (distance(out[out.length - 1], last) < minLength && out.length > 1) out.pop();
  out.push(last);
  return out;
}

/** Split any segment longer than `maxLength` into equal sub-stitches. */
export function enforceMaxStitchLength(points: readonly Point[], maxLength: number): Point[] {
  if (points.length < 2 || maxLength <= 0) return [...points];
  return densifyPath(points, maxLength);
}
