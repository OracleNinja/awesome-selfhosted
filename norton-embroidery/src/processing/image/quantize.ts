/**
 * Colour reduction.
 *
 * Artwork is reduced to a small palette by weighted k-means over a colour
 * histogram, seeded with a deterministic k-means++ pass.
 *
 * Seeding matters more than it looks. A median-cut seed splits by pixel
 * population, so a logo sitting on a large white background spends its early
 * splits subdividing the background and can end up merging two genuinely
 * different logo colours into one thread. Farthest-point seeding picks the most
 * distinct colours first, which is exactly what a small thread palette needs.
 *
 * Everything here is deterministic: the same image always produces the same
 * palette, because an operator must be able to reproduce a job.
 */

import type { RasterImage } from './raster';

export interface QuantizedColor {
  r: number;
  g: number;
  b: number;
  /** Number of opaque source pixels assigned to this colour. */
  count: number;
  /** Share of opaque pixels, 0..1. */
  share: number;
}

export interface QuantizeResult {
  colors: QuantizedColor[];
  /** Per-pixel palette index; -1 for pixels treated as transparent. */
  labels: Int16Array;
  width: number;
  height: number;
  /** Pixels ignored because their alpha was below the threshold. */
  transparentPixels: number;
}

const ALPHA_THRESHOLD = 128;

/** Bits per channel used when bucketing colours. 5 bits => 32768 buckets. */
const HIST_BITS = 5;
const HIST_SHIFT = 8 - HIST_BITS;
const HIST_SIZE = 1 << (HIST_BITS * 3);

interface Bucket {
  r: number;
  g: number;
  b: number;
  count: number;
}

/** Group opaque pixels into colour buckets, each holding its mean colour. */
function histogram(
  opaque: readonly number[],
  r: Uint8Array,
  g: Uint8Array,
  b: Uint8Array,
): Bucket[] {
  const counts = new Int32Array(HIST_SIZE);
  const sumR = new Float64Array(HIST_SIZE);
  const sumG = new Float64Array(HIST_SIZE);
  const sumB = new Float64Array(HIST_SIZE);

  for (const p of opaque) {
    const key =
      ((r[p] >> HIST_SHIFT) << (HIST_BITS * 2)) |
      ((g[p] >> HIST_SHIFT) << HIST_BITS) |
      (b[p] >> HIST_SHIFT);
    counts[key]++;
    sumR[key] += r[p];
    sumG[key] += g[p];
    sumB[key] += b[p];
  }

  const buckets: Bucket[] = [];
  for (let key = 0; key < HIST_SIZE; key++) {
    const c = counts[key];
    if (c === 0) continue;
    buckets.push({ r: sumR[key] / c, g: sumG[key] / c, b: sumB[key] / c, count: c });
  }
  // Descending population, then colour, so the result never depends on Map order.
  buckets.sort((x, y) => y.count - x.count || x.r - y.r || x.g - y.g || x.b - y.b);
  return buckets;
}

const sqDist = (
  a: { r: number; g: number; b: number },
  bb: { r: number; g: number; b: number },
): number => (a.r - bb.r) ** 2 + (a.g - bb.g) ** 2 + (a.b - bb.b) ** 2;

/**
 * Deterministic k-means++ seeding: start from the most common colour, then
 * repeatedly take the bucket whose distance from everything chosen so far,
 * weighted by how much of the image it covers, is greatest.
 */
function seedCentroids(buckets: Bucket[], k: number): Array<{ r: number; g: number; b: number }> {
  const centroids: Array<{ r: number; g: number; b: number }> = [
    { r: buckets[0].r, g: buckets[0].g, b: buckets[0].b },
  ];
  const minDist = buckets.map((bk) => sqDist(bk, centroids[0]));

  while (centroids.length < k) {
    let bestIndex = -1;
    let bestScore = 0;
    for (let i = 0; i < buckets.length; i++) {
      // Weight by log of population so a large flat area outranks a stray
      // pixel of an extreme colour, without letting it dominate outright.
      const score = minDist[i] * Math.log2(buckets[i].count + 1);
      if (score > bestScore) {
        bestScore = score;
        bestIndex = i;
      }
    }
    if (bestIndex < 0) break; // every remaining bucket duplicates a centroid
    const chosen = buckets[bestIndex];
    centroids.push({ r: chosen.r, g: chosen.g, b: chosen.b });
    for (let i = 0; i < buckets.length; i++) {
      const d = sqDist(buckets[i], chosen);
      if (d < minDist[i]) minDist[i] = d;
    }
  }
  return centroids;
}

export function quantize(img: RasterImage, maxColors: number): QuantizeResult {
  const n = img.width * img.height;
  const r = new Uint8Array(n);
  const g = new Uint8Array(n);
  const b = new Uint8Array(n);
  const opaque: number[] = [];
  let transparentPixels = 0;

  for (let p = 0; p < n; p++) {
    const i = p * 4;
    r[p] = img.data[i];
    g[p] = img.data[i + 1];
    b[p] = img.data[i + 2];
    if (img.data[i + 3] >= ALPHA_THRESHOLD) opaque.push(p);
    else transparentPixels++;
  }

  const labels = new Int16Array(n).fill(-1);
  if (opaque.length === 0) {
    return { colors: [], labels, width: img.width, height: img.height, transparentPixels };
  }

  // --- cluster over the colour histogram --------------------------------
  const buckets = histogram(opaque, r, g, b);
  let centroids = seedCentroids(buckets, Math.min(maxColors, buckets.length));

  const counts = new Array(centroids.length).fill(0);
  for (let iter = 0; iter < 24; iter++) {
    const sums = centroids.map(() => ({ r: 0, g: 0, b: 0, n: 0 }));
    for (const bucket of buckets) {
      let best = 0;
      let bestD = Infinity;
      for (let c = 0; c < centroids.length; c++) {
        const d = sqDist(bucket, centroids[c]);
        if (d < bestD) {
          bestD = d;
          best = c;
        }
      }
      const s = sums[best];
      s.r += bucket.r * bucket.count;
      s.g += bucket.g * bucket.count;
      s.b += bucket.b * bucket.count;
      s.n += bucket.count;
    }
    let moved = 0;
    centroids = centroids.map((c, i) => {
      const s = sums[i];
      if (s.n === 0) return c;
      const nc = { r: s.r / s.n, g: s.g / s.n, b: s.b / s.n };
      moved += Math.abs(nc.r - c.r) + Math.abs(nc.g - c.g) + Math.abs(nc.b - c.b);
      return nc;
    });
    for (let i = 0; i < counts.length; i++) counts[i] = sums[i].n;
    if (moved < 0.5) break;
  }

  // Assign every opaque pixel to its nearest final centroid.
  for (const p of opaque) {
    let best = 0;
    let bestD = Infinity;
    for (let c = 0; c < centroids.length; c++) {
      const dr = r[p] - centroids[c].r;
      const dg = g[p] - centroids[c].g;
      const db = b[p] - centroids[c].b;
      const d = dr * dr + dg * dg + db * db;
      if (d < bestD) {
        bestD = d;
        best = c;
      }
    }
    labels[p] = best;
  }
  counts.fill(0);
  for (const p of opaque) counts[labels[p]]++;

  // Drop empty clusters and re-index labels.
  const keep: number[] = [];
  for (let i = 0; i < centroids.length; i++) if (counts[i] > 0) keep.push(i);
  const remap = new Int16Array(centroids.length).fill(-1);
  keep.forEach((oldIndex, newIndex) => {
    remap[oldIndex] = newIndex;
  });
  for (const p of opaque) labels[p] = remap[labels[p]];

  const totalOpaque = opaque.length;
  const colors: QuantizedColor[] = keep.map((i) => ({
    r: Math.round(centroids[i].r),
    g: Math.round(centroids[i].g),
    b: Math.round(centroids[i].b),
    count: counts[i],
    share: counts[i] / totalOpaque,
  }));

  // Sort by descending coverage, keeping labels in sync.
  const order = colors.map((_, i) => i).sort((a, c) => colors[c].count - colors[a].count);
  const sortRemap = new Int16Array(colors.length);
  order.forEach((oldIndex, newIndex) => {
    sortRemap[oldIndex] = newIndex;
  });
  for (const p of opaque) labels[p] = sortRemap[labels[p]];

  return {
    colors: order.map((i) => colors[i]),
    labels,
    width: img.width,
    height: img.height,
    transparentPixels,
  };
}

/**
 * Estimate how many distinct colours the artwork really uses, by quantizing to
 * a generous palette and counting clusters that both cover a meaningful area
 * and are perceptually distinct from an already-counted cluster.
 */
export function estimateDistinctColors(img: RasterImage, maxProbe = 16): number {
  const q = quantize(img, maxProbe);
  const kept: QuantizedColor[] = [];
  for (const c of q.colors) {
    if (c.share < 0.005) continue;
    const dup = kept.some(
      (k) => Math.abs(k.r - c.r) < 18 && Math.abs(k.g - c.g) < 18 && Math.abs(k.b - c.b) < 18,
    );
    if (!dup) kept.push(c);
  }
  return Math.max(1, kept.length);
}
