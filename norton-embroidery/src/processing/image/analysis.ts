/**
 * Artwork analysis.
 *
 * Produces an honest description of what the artwork is and how well it is
 * likely to digitize. Where the pipeline cannot determine something it says so
 * via `confidence` and `uncertain` fields rather than guessing.
 */

import { fitWithin, hasAlpha, fullyTransparentPixels, sobelMagnitude, type RasterImage } from './raster';
import { estimateDistinctColors, quantize, type QuantizeResult } from './quantize';
import { detectBackgroundColorIndex, extractRegions, type Region } from './segment';

export type Confidence = 'high' | 'medium' | 'low';

export interface DetectedColor {
  r: number;
  g: number;
  b: number;
  hex: string;
  /** Share of opaque pixels, 0..1. */
  share: number;
  pixelCount: number;
  isBackground: boolean;
}

export interface RegionSummary {
  id: number;
  colorIndex: number;
  pixelCount: number;
  /** Share of the opaque artwork area. */
  areaShare: number;
  /** Widest point of the region, in source pixels. */
  widthPx: number;
  elongation: number;
  solidity: number;
  /** Classification the digitizer will act on. */
  suggestedStitchType: 'fill' | 'satin' | 'running';
  /** Set when the region is so thin it may not stitch cleanly. */
  tooSmall: boolean;
}

export interface AnalysisWarning {
  code: string;
  severity: 'INFO' | 'WARNING';
  message: string;
}

export interface ArtworkAnalysis {
  sourceDimensions: { width: number; height: number };
  /** Size of the reduced image the analysis actually ran on. */
  analysisDimensions: { width: number; height: number };
  detectedColors: DetectedColor[];
  /** Colours covering at least 2% of the artwork. */
  dominantColors: DetectedColor[];
  /** Best estimate of distinct colours in the original, before reduction. */
  estimatedSourceColors: number;
  backgroundDetected: boolean;
  backgroundColorIndex: number | null;
  foregroundRegions: RegionSummary[];
  /** Regions thin enough to be stitched as lines. */
  lineRegionCount: number;
  /** Regions narrow enough for satin columns. */
  satinRegionCount: number;
  fillRegionCount: number;
  /**
   * Text detection is NOT implemented. This field is always
   * `{ detected: false, confidence: 'low' }` so the UI can state plainly that
   * lettering must be identified by the operator.
   */
  textRegions: { detected: false; confidence: 'low'; note: string };
  hasTransparency: boolean;
  transparentPixelShare: number;
  /** 0..1; share of pixels sitting on a strong edge. */
  edgeDensity: number;
  /** 0..1; share of pixels whose local neighbourhood is a smooth ramp. */
  gradientScore: number;
  /** 0..1. Higher means harder to digitize automatically. */
  complexityScore: number;
  /** 0..100. Higher is better suited to automatic digitization. */
  embroiderySuitabilityScore: number;
  suitability: 'good' | 'workable' | 'manual-cleanup-likely' | 'not-suitable';
  confidence: Confidence;
  warnings: AnalysisWarning[];
  recommendations: string[];
  /** Retained so the digitizer does not have to redo the work. */
  quantized: QuantizeResult;
  regions: Region[];
  /** Scale factor from analysis pixels back to source pixels. */
  analysisScale: number;
}

export interface AnalysisOptions {
  /** Longest side of the image the analysis runs on. */
  maxAnalysisSide: number;
  /** Palette size used for segmentation. */
  colorCount: number;
  /** Regions below this many analysis pixels are discarded. */
  minRegionPixels: number;
  /** Physical width the design will be stitched at, in 0.1 mm units. */
  targetWidthUnits: number;
  /** Physical height the design will be stitched at, in 0.1 mm units. */
  targetHeightUnits: number;
}

export const DEFAULT_ANALYSIS_OPTIONS: Omit<AnalysisOptions, 'targetWidthUnits' | 'targetHeightUnits'> = {
  maxAnalysisSide: 320,
  colorCount: 6,
  minRegionPixels: 24,
};

const hex = (r: number, g: number, b: number): string =>
  `#${[r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')}`;

/**
 * Minimum stitchable feature width, in millimetres. Below roughly 1 mm a
 * column of satin will not hold and the machine will punch holes in the fabric.
 */
export const MIN_FEATURE_MM = 1.0;
/** Widest column still sewn as satin rather than fill, in millimetres. */
export const SATIN_MAX_MM = 10;

export function analyzeArtwork(source: RasterImage, options: AnalysisOptions): ArtworkAnalysis {
  const img = fitWithin(source, options.maxAnalysisSide);
  const analysisScale = source.width / img.width;

  const transparent = fullyTransparentPixels(img);
  const transparentShare = transparent / (img.width * img.height);
  const alpha = hasAlpha(img);

  const q = quantize(img, options.colorCount);
  const backgroundColorIndex = detectBackgroundColorIndex(q);
  const { regions, discarded, discardedPixels } = extractRegions(q, options.minRegionPixels);

  const opaquePixels = Math.max(1, img.width * img.height - transparent);

  const detectedColors: DetectedColor[] = q.colors.map((c, i) => ({
    r: c.r,
    g: c.g,
    b: c.b,
    hex: hex(c.r, c.g, c.b),
    share: c.share,
    pixelCount: c.count,
    isBackground: i === backgroundColorIndex,
  }));

  // --- physical scale ---------------------------------------------------
  // Millimetres per analysis pixel, in each axis; used to decide stitch types.
  const mmPerPxX = options.targetWidthUnits / 10 / img.width;
  const mmPerPxY = options.targetHeightUnits / 10 / img.height;
  const mmPerPx = (mmPerPxX + mmPerPxY) / 2;

  const foregroundRegions: RegionSummary[] = [];
  let lineRegionCount = 0;
  let satinRegionCount = 0;
  let fillRegionCount = 0;
  let tooSmallCount = 0;

  for (const region of regions) {
    if (backgroundColorIndex !== null && region.colorIndex === backgroundColorIndex) continue;
    const widthMm = region.maxHalfWidth * 2 * mmPerPx;
    const typicalWidthMm = region.medianHalfWidth * 2 * mmPerPx;
    const tooSmall = widthMm < MIN_FEATURE_MM;

    let suggested: 'fill' | 'satin' | 'running';
    if (widthMm < MIN_FEATURE_MM * 1.6) suggested = 'running';
    else if (typicalWidthMm <= SATIN_MAX_MM && region.elongation >= 1.8) suggested = 'satin';
    else suggested = 'fill';

    if (suggested === 'running') lineRegionCount++;
    else if (suggested === 'satin') satinRegionCount++;
    else fillRegionCount++;
    if (tooSmall) tooSmallCount++;

    foregroundRegions.push({
      id: region.id,
      colorIndex: region.colorIndex,
      pixelCount: region.pixelCount,
      areaShare: region.pixelCount / opaquePixels,
      widthPx: region.maxHalfWidth * 2,
      elongation: region.elongation,
      solidity: region.solidity,
      suggestedStitchType: suggested,
      tooSmall,
    });
  }

  // --- edge / gradient measures ----------------------------------------
  const edges = sobelMagnitude(img);
  let strongEdges = 0;
  for (let i = 0; i < edges.length; i++) if (edges[i] > 0.25) strongEdges++;
  const edgeDensity = strongEdges / edges.length;

  const gradientScore = measureGradients(img);
  const estimatedSourceColors = estimateDistinctColors(img);

  // --- complexity -------------------------------------------------------
  // Each term is 0..1; the weights are a judgement call, documented in the UI.
  const regionTerm = Math.min(1, foregroundRegions.length / 40);
  const colorTerm = Math.min(1, estimatedSourceColors / 14);
  const edgeTerm = Math.min(1, edgeDensity / 0.25);
  const gradientTerm = gradientScore;
  const smallTerm = foregroundRegions.length
    ? tooSmallCount / foregroundRegions.length
    : 0;
  const complexityScore = clamp01(
    0.25 * regionTerm + 0.25 * colorTerm + 0.2 * edgeTerm + 0.2 * gradientTerm + 0.1 * smallTerm,
  );

  const suitabilityScore = Math.round(clamp01(1 - complexityScore) * 100);
  const suitability: ArtworkAnalysis['suitability'] =
    suitabilityScore >= 75
      ? 'good'
      : suitabilityScore >= 55
        ? 'workable'
        : suitabilityScore >= 35
          ? 'manual-cleanup-likely'
          : 'not-suitable';

  // --- warnings ---------------------------------------------------------
  const warnings: AnalysisWarning[] = [];
  const recommendations: string[] = [];

  if (foregroundRegions.length === 0) {
    warnings.push({
      code: 'artwork.empty',
      severity: 'WARNING',
      message:
        'No stitchable regions were found. The artwork may be blank, fully transparent, or made ' +
        'entirely of one background colour.',
    });
  }

  if (gradientScore > 0.35) {
    warnings.push({
      code: 'artwork.gradients',
      severity: 'WARNING',
      message: `Smooth colour gradients cover about ${(gradientScore * 100).toFixed(0)}% of the artwork. Thread cannot blend, so gradients are reduced to flat colour bands.`,
    });
    recommendations.push('Flatten gradients to solid colours in the artwork before digitizing.');
  }

  if (edgeDensity > 0.28) {
    warnings.push({
      code: 'artwork.photographic',
      severity: 'WARNING',
      message: `High edge detail (${(edgeDensity * 100).toFixed(0)}% of pixels sit on a strong edge). This looks photographic rather than like a logo; automatic digitizing will lose detail.`,
    });
    recommendations.push('Photographs need manual conversion to a small number of flat colour areas first.');
  }

  if (tooSmallCount > 0) {
    warnings.push({
      code: 'artwork.small-details',
      severity: 'WARNING',
      message: `${tooSmallCount} region(s) are narrower than ${MIN_FEATURE_MM} mm at the requested size. These will not reproduce cleanly and are stitched as single lines.`,
    });
    recommendations.push(
      `Increase the design size, or remove details thinner than ${MIN_FEATURE_MM} mm from the artwork.`,
    );
  }

  if (discarded > 0) {
    warnings.push({
      code: 'artwork.speckles',
      severity: 'INFO',
      message: `${discarded} tiny fragment(s) totalling ${discardedPixels} pixels were ignored as noise.`,
    });
  }

  if (estimatedSourceColors > options.colorCount) {
    warnings.push({
      code: 'artwork.color-reduction',
      severity: 'INFO',
      message: `The artwork appears to use about ${estimatedSourceColors} distinct colours; it was reduced to ${q.colors.length} for stitching.`,
    });
    recommendations.push('Raise the colour count if important detail was merged, but expect one thread change per colour.');
  }

  if (alpha && transparentShare > 0.02) {
    warnings.push({
      code: 'artwork.transparency',
      severity: 'INFO',
      message: `${(transparentShare * 100).toFixed(0)}% of the artwork is transparent. Transparent areas are left unstitched.`,
    });
  }

  if (backgroundColorIndex !== null) {
    recommendations.push('A solid background colour was detected and excluded from stitching.');
  } else if (!alpha) {
    warnings.push({
      code: 'artwork.no-background',
      severity: 'INFO',
      message:
        'No solid background was detected, so every colour area is treated as something to stitch. ' +
        'Remove the background from the artwork if it should not be sewn.',
    });
  }

  // Confidence in the analysis itself.
  const confidence: Confidence =
    edgeDensity > 0.3 || gradientScore > 0.5
      ? 'low'
      : foregroundRegions.length > 0 && estimatedSourceColors <= 8
        ? 'high'
        : 'medium';

  return {
    sourceDimensions: { width: source.width, height: source.height },
    analysisDimensions: { width: img.width, height: img.height },
    detectedColors,
    dominantColors: detectedColors.filter((c) => c.share >= 0.02),
    estimatedSourceColors,
    backgroundDetected: backgroundColorIndex !== null,
    backgroundColorIndex,
    foregroundRegions,
    lineRegionCount,
    satinRegionCount,
    fillRegionCount,
    textRegions: {
      detected: false,
      confidence: 'low',
      note:
        'Automatic text detection is not implemented in this version. Lettering is digitized as ' +
        'ordinary shapes; check small type carefully and consider replacing it with true satin columns.',
    },
    hasTransparency: alpha,
    transparentPixelShare: transparentShare,
    edgeDensity,
    gradientScore,
    complexityScore,
    embroiderySuitabilityScore: suitabilityScore,
    suitability,
    confidence,
    warnings,
    recommendations,
    quantized: q,
    regions,
    analysisScale,
  };
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

/**
 * Share of pixels that sit inside a smooth colour ramp: the local neighbourhood
 * changes, but changes gently and consistently in one direction.
 */
function measureGradients(img: RasterImage): number {
  const { width: w, height: h, data } = img;
  if (w < 3 || h < 3) return 0;
  let ramp = 0;
  let considered = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = (y * w + x) * 4;
      if (data[i + 3] < 128) continue;
      considered++;
      const left = (y * w + x - 1) * 4;
      const right = (y * w + x + 1) * 4;
      const up = ((y - 1) * w + x) * 4;
      const down = ((y + 1) * w + x) * 4;
      const dxs = channelDelta(data, left, right);
      const dys = channelDelta(data, up, down);
      const mag = Math.hypot(dxs, dys);
      // A ramp changes slowly (not an edge) but is not flat either.
      if (mag > 3 && mag < 40) ramp++;
    }
  }
  return considered === 0 ? 0 : ramp / considered;
}

function channelDelta(data: Uint8ClampedArray, a: number, b: number): number {
  return (
    (Math.abs(data[a] - data[b]) + Math.abs(data[a + 1] - data[b + 1]) + Math.abs(data[a + 2] - data[b + 2])) / 3
  );
}
