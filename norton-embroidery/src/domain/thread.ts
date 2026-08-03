/**
 * Thread abstraction.
 *
 * The application distinguishes three different colours:
 *   - artwork colour: the RGB sampled from the customer's image
 *   - thread colour:  the RGB of the physical thread that was selected
 *   - display colour: what the stitch renderer draws (always the thread colour,
 *     so the preview shows what will actually be sewn)
 *
 * Only thread charts with verified data are bundled. See `threadCharts` below.
 */

export interface Thread {
  /** Stable id: `${chartId}:${code}`. */
  id: string;
  /** Manufacturer / brand, e.g. "Brother". */
  manufacturer: string;
  /** Chart the thread belongs to, e.g. "Brother PEC (machine palette)". */
  chart: string;
  /** Manufacturer catalogue code. */
  code: string;
  name: string;
  r: number;
  g: number;
  b: number;
}

export interface ThreadChart {
  id: string;
  name: string;
  manufacturer: string;
  /** Human readable note about where the data came from. */
  provenance: string;
  threads: Thread[];
}

export function threadHex(t: { r: number; g: number; b: number }): string {
  const h = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return `#${h(t.r)}${h(t.g)}${h(t.b)}`;
}

export function rgbToInt(r: number, g: number, b: number): number {
  return ((r & 0xff) << 16) | ((g & 0xff) << 8) | (b & 0xff);
}

export function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  return {
    r: parseInt(full.slice(0, 2), 16),
    g: parseInt(full.slice(2, 4), 16),
    b: parseInt(full.slice(4, 6), 16),
  };
}

/**
 * Perceptual "redmean" colour distance. This is the same metric used by
 * pyembroidery / libembroidery when mapping colours onto a machine palette, so
 * our palette indices agree with what other embroidery software produces.
 */
export function colorDistance(
  r1: number,
  g1: number,
  b1: number,
  r2: number,
  g2: number,
  b2: number,
): number {
  const rMean = (r1 + r2) / 2;
  const dr = r1 - r2;
  const dg = g1 - g2;
  const db = b1 - b2;
  return Math.sqrt(
    (((512 + rMean) * dr * dr) / 256) + 4 * dg * dg + (((767 - rMean) * db * db) / 256),
  );
}

export function nearestThread(
  color: { r: number; g: number; b: number },
  threads: readonly Thread[],
): Thread {
  let best = threads[0];
  let bestDist = Infinity;
  for (const t of threads) {
    const d = colorDistance(color.r, color.g, color.b, t.r, t.g, t.b);
    if (d < bestDist) {
      bestDist = d;
      best = t;
    }
  }
  return best;
}

/**
 * The 64-colour Brother machine palette used by the PEC block of PES files.
 * These are the colours a Brother machine (including the SE700) displays for
 * each colour slot. Values verified against pyembroidery's `EmbThreadPec`
 * chart, which is derived from the format specification.
 *
 * Index 0 is reserved/unused by the format, so codes here run 1..64 and the
 * array index of a thread in `PEC_THREADS` is its palette index minus one.
 */
const PEC_RAW: Array<[number, number, number, string]> = [
  [14, 31, 124, 'Prussian Blue'],
  [10, 85, 163, 'Blue'],
  [0, 135, 119, 'Teal Green'],
  [75, 107, 175, 'Cornflower Blue'],
  [237, 23, 31, 'Red'],
  [209, 92, 0, 'Reddish Brown'],
  [145, 54, 151, 'Magenta'],
  [228, 154, 203, 'Light Lilac'],
  [145, 95, 172, 'Lilac'],
  [158, 214, 125, 'Mint Green'],
  [232, 169, 0, 'Deep Gold'],
  [254, 186, 53, 'Orange'],
  [255, 255, 0, 'Yellow'],
  [112, 188, 31, 'Lime Green'],
  [186, 152, 0, 'Brass'],
  [168, 168, 168, 'Silver'],
  [125, 111, 0, 'Russet Brown'],
  [255, 255, 179, 'Cream Brown'],
  [79, 85, 86, 'Pewter'],
  [0, 0, 0, 'Black'],
  [11, 61, 145, 'Ultramarine'],
  [119, 1, 118, 'Royal Purple'],
  [41, 49, 51, 'Dark Gray'],
  [42, 19, 1, 'Dark Brown'],
  [246, 74, 138, 'Deep Rose'],
  [178, 118, 36, 'Light Brown'],
  [252, 187, 197, 'Salmon Pink'],
  [254, 55, 15, 'Vermilion'],
  [240, 240, 240, 'White'],
  [106, 28, 138, 'Violet'],
  [168, 221, 196, 'Seacrest'],
  [37, 132, 187, 'Sky Blue'],
  [254, 179, 67, 'Pumpkin'],
  [255, 243, 107, 'Cream Yellow'],
  [208, 166, 96, 'Khaki'],
  [209, 84, 0, 'Clay Brown'],
  [102, 186, 73, 'Leaf Green'],
  [19, 74, 70, 'Peacock Blue'],
  [135, 135, 135, 'Gray'],
  [216, 204, 198, 'Warm Gray'],
  [67, 86, 7, 'Dark Olive'],
  [253, 217, 222, 'Flesh Pink'],
  [249, 147, 188, 'Pink'],
  [0, 56, 34, 'Deep Green'],
  [178, 175, 212, 'Lavender'],
  [104, 106, 176, 'Wisteria Violet'],
  [239, 227, 185, 'Beige'],
  [247, 56, 102, 'Carmine'],
  [181, 75, 100, 'Amber Red'],
  [19, 43, 26, 'Olive Green'],
  [199, 1, 86, 'Dark Fuchsia'],
  [254, 158, 50, 'Tangerine'],
  [168, 222, 235, 'Light Blue'],
  [0, 103, 62, 'Emerald Green'],
  [78, 41, 144, 'Purple'],
  [47, 126, 32, 'Moss Green'],
  [255, 204, 204, 'Flesh Pink'],
  [255, 217, 17, 'Harvest Gold'],
  [9, 91, 166, 'Electric Blue'],
  [240, 249, 112, 'Lemon Yellow'],
  [227, 243, 91, 'Fresh Green'],
  [255, 153, 0, 'Orange'],
  [255, 240, 141, 'Cream Yellow'],
  [255, 200, 200, 'Applique'],
];

export const PEC_CHART_ID = 'brother-pec';

export const PEC_THREADS: Thread[] = PEC_RAW.map(([r, g, b, name], i) => ({
  id: `${PEC_CHART_ID}:${i + 1}`,
  manufacturer: 'Brother',
  chart: 'Brother PEC machine palette',
  code: String(i + 1),
  name,
  r,
  g,
  b,
}));

/**
 * Palette index (1..64) that a Brother machine uses for a given thread of this
 * chart. Returns 0 for threads outside the chart.
 */
export function pecPaletteIndex(thread: Thread): number {
  if (!thread.id.startsWith(`${PEC_CHART_ID}:`)) return 0;
  const n = Number(thread.code);
  return Number.isFinite(n) ? n : 0;
}

export const threadCharts: ThreadChart[] = [
  {
    id: PEC_CHART_ID,
    name: 'Brother PEC machine palette (64 colours)',
    manufacturer: 'Brother',
    provenance:
      'Colour table defined by the Brother PEC/PES file format itself; values cross-checked against pyembroidery 1.5.1.',
    threads: PEC_THREADS,
  },
];

/**
 * Charts the application knows about but does NOT bundle, because no verified
 * colour data is available in this repository. Fabricating thread codes would
 * make the operator order the wrong cones, so these are surfaced to the user as
 * "import required" instead of being guessed at.
 */
export const unbundledCharts = [
  { id: 'madeira-polyneon', name: 'Madeira Polyneon', manufacturer: 'Madeira' },
  { id: 'madeira-classic-rayon', name: 'Madeira Classic Rayon', manufacturer: 'Madeira' },
  { id: 'isacord-40', name: 'Isacord 40', manufacturer: 'Isacord' },
] as const;

export function getChart(id: string): ThreadChart | undefined {
  return threadCharts.find((c) => c.id === id);
}

/** Register a chart imported by the user at runtime (CSV/JSON upload). */
export function registerChart(chart: ThreadChart): void {
  const existing = threadCharts.findIndex((c) => c.id === chart.id);
  if (existing >= 0) threadCharts[existing] = chart;
  else threadCharts.push(chart);
}

export function allThreads(): Thread[] {
  return threadCharts.flatMap((c) => c.threads);
}

export function searchThreads(query: string, chartId?: string): Thread[] {
  const q = query.trim().toLowerCase();
  const pool = chartId ? (getChart(chartId)?.threads ?? []) : allThreads();
  if (!q) return pool;
  return pool.filter(
    (t) =>
      t.name.toLowerCase().includes(q) ||
      t.code.toLowerCase().includes(q) ||
      t.manufacturer.toLowerCase().includes(q) ||
      threadHex(t).toLowerCase().includes(q),
  );
}
