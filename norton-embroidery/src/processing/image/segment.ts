/**
 * Region segmentation: turn a quantized label image into connected regions,
 * each of which becomes a candidate embroidery object.
 */

import { distanceTransform } from './raster';
import type { QuantizeResult } from './quantize';

export interface Region {
  id: number;
  /** Palette index of the colour this region belongs to. */
  colorIndex: number;
  /** Binary mask over the full image; 1 inside the region. */
  mask: Uint8Array;
  width: number;
  height: number;
  pixelCount: number;
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  /** Widest half-width in pixels (max of the distance transform). */
  maxHalfWidth: number;
  /** Median half-width in pixels over the region's interior. */
  medianHalfWidth: number;
  /** pixelCount / bounding box area, 0..1. */
  solidity: number;
  /** longer bbox side / shorter bbox side. */
  elongation: number;
}

/** Remove specks and fill pinholes so tracing produces sewable geometry. */
export function cleanMask(mask: Uint8Array, w: number, h: number, radius = 1): Uint8Array {
  let cur = mask;
  for (let r = 0; r < radius; r++) cur = morph(cur, w, h, 'erode');
  for (let r = 0; r < radius * 2; r++) cur = morph(cur, w, h, 'dilate');
  for (let r = 0; r < radius; r++) cur = morph(cur, w, h, 'erode');
  return cur;
}

export function morph(mask: Uint8Array, w: number, h: number, op: 'erode' | 'dilate'): Uint8Array {
  const out = new Uint8Array(mask.length);
  const want = op === 'erode' ? 1 : 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      let result = mask[i];
      if (op === 'erode' ? mask[i] === 1 : mask[i] === 0) {
        for (let dy = -1; dy <= 1 && result === mask[i]; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            if (dx === 0 && dy === 0) continue;
            const nx = x + dx;
            const ny = y + dy;
            // Treat outside the image as background.
            const v = nx < 0 || ny < 0 || nx >= w || ny >= h ? 0 : mask[ny * w + nx];
            if (op === 'erode' ? v === 0 : v === 1) {
              result = want === 1 ? 0 : 1;
              break;
            }
          }
        }
      }
      out[i] = result;
    }
  }
  return out;
}

/**
 * 4-connected labelling of each colour class. Regions smaller than
 * `minPixels` are discarded (they cannot be stitched reliably anyway) but the
 * count of discarded regions is returned so the caller can warn about them.
 */
export function extractRegions(
  q: QuantizeResult,
  minPixels: number,
): { regions: Region[]; discarded: number; discardedPixels: number } {
  const { width: w, height: h, labels } = q;
  const visited = new Uint8Array(w * h);
  const regions: Region[] = [];
  let discarded = 0;
  let discardedPixels = 0;
  let nextId = 0;

  const stack = new Int32Array(w * h);

  for (let start = 0; start < labels.length; start++) {
    if (visited[start] || labels[start] < 0) continue;
    const colorIndex = labels[start];
    let sp = 0;
    stack[sp++] = start;
    visited[start] = 1;
    const pixels: number[] = [];
    let minX = w;
    let minY = h;
    let maxX = 0;
    let maxY = 0;

    while (sp > 0) {
      const p = stack[--sp];
      pixels.push(p);
      const x = p % w;
      const y = (p / w) | 0;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;

      if (x > 0) pushIf(p - 1);
      if (x < w - 1) pushIf(p + 1);
      if (y > 0) pushIf(p - w);
      if (y < h - 1) pushIf(p + w);
    }

    function pushIf(np: number): void {
      if (!visited[np] && labels[np] === colorIndex) {
        visited[np] = 1;
        stack[sp++] = np;
      }
    }

    if (pixels.length < minPixels) {
      discarded++;
      discardedPixels += pixels.length;
      continue;
    }

    const mask = new Uint8Array(w * h);
    for (const p of pixels) mask[p] = 1;

    const dt = distanceTransform(mask, w, h);
    let maxHalfWidth = 0;
    const widths: number[] = [];
    for (const p of pixels) {
      const d = dt[p];
      if (d > maxHalfWidth) maxHalfWidth = d;
      widths.push(d);
    }
    widths.sort((a, b) => a - b);
    // Median over the upper half only: the interior, not the boundary ring.
    const upper = widths.slice(Math.floor(widths.length / 2));
    const medianHalfWidth = upper.length ? upper[Math.floor(upper.length / 2)] : 0;

    const bw = maxX - minX + 1;
    const bh = maxY - minY + 1;
    regions.push({
      id: nextId++,
      colorIndex,
      mask,
      width: w,
      height: h,
      pixelCount: pixels.length,
      minX,
      minY,
      maxX,
      maxY,
      maxHalfWidth,
      medianHalfWidth,
      solidity: pixels.length / (bw * bh),
      elongation: Math.max(bw, bh) / Math.max(1, Math.min(bw, bh)),
    });
  }

  regions.sort((a, b) => b.pixelCount - a.pixelCount);
  return { regions, discarded, discardedPixels };
}

/**
 * Decide whether a colour class is the background: a region is background when
 * it is the largest region of its colour AND it touches a majority of the
 * image border.
 */
export function detectBackgroundColorIndex(q: QuantizeResult): number | null {
  const { width: w, height: h, labels } = q;
  if (q.colors.length === 0) return null;

  const borderCounts = new Map<number, number>();
  let borderTotal = 0;
  const mark = (p: number): void => {
    const l = labels[p];
    if (l < 0) return;
    borderCounts.set(l, (borderCounts.get(l) ?? 0) + 1);
    borderTotal++;
  };
  for (let x = 0; x < w; x++) {
    mark(x);
    mark((h - 1) * w + x);
  }
  for (let y = 0; y < h; y++) {
    mark(y * w);
    mark(y * w + w - 1);
  }
  if (borderTotal === 0) return null; // fully transparent border == cut-out artwork

  let bestIndex = -1;
  let bestCount = 0;
  for (const [index, count] of borderCounts) {
    if (count > bestCount) {
      bestCount = count;
      bestIndex = index;
    }
  }
  // Require a clear majority of the border, otherwise it is not a background.
  return bestCount / borderTotal >= 0.75 ? bestIndex : null;
}
