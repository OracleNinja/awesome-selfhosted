import { describe, expect, it } from 'vitest';
import { analyzeArtwork, DEFAULT_ANALYSIS_OPTIONS } from '../src/processing/image/analysis';
import { quantize } from '../src/processing/image/quantize';
import { mmToUnits } from '../src/domain/units';
import {
  blankArtwork,
  fromPngBuffer,
  gradientArtwork,
  ringArtwork,
  simpleSquare,
  thinBarArtwork,
  threeColorArtwork,
  toPngBuffer,
  transparentArtwork,
} from './fixtures';

const options = (w = 90, h = 90) => ({
  ...DEFAULT_ANALYSIS_OPTIONS,
  targetWidthUnits: mmToUnits(w),
  targetHeightUnits: mmToUnits(h),
});

describe('image upload', () => {
  it('round-trips a real PNG file without changing a pixel', () => {
    const original = threeColorArtwork();
    const png = toPngBuffer(original);
    // A real PNG file starts with the 8-byte signature.
    expect([...png.subarray(0, 4)]).toEqual([0x89, 0x50, 0x4e, 0x47]);

    const decoded = fromPngBuffer(png);
    expect(decoded.width).toBe(original.width);
    expect(decoded.height).toBe(original.height);
    expect(Array.from(decoded.data)).toEqual(Array.from(original.data));
  });
});

describe('colour detection', () => {
  it('finds the three artwork colours plus the background', () => {
    const q = quantize(threeColorArtwork(), 6);
    const hexes = q.colors.map((c) => `${c.r},${c.g},${c.b}`);
    expect(q.colors.length).toBeGreaterThanOrEqual(4);

    // Each planted colour should be present within a small tolerance.
    const near = (r: number, g: number, b: number): boolean =>
      q.colors.some((c) => Math.abs(c.r - r) < 25 && Math.abs(c.g - g) < 25 && Math.abs(c.b - b) < 25);
    expect(near(220, 30, 40), `red missing from ${hexes}`).toBe(true);
    expect(near(30, 150, 70), `green missing from ${hexes}`).toBe(true);
    expect(near(20, 60, 180), `blue missing from ${hexes}`).toBe(true);
    expect(near(255, 255, 255), `white missing from ${hexes}`).toBe(true);
  });

  it('is deterministic', () => {
    const a = quantize(threeColorArtwork(), 5).colors;
    const b = quantize(threeColorArtwork(), 5).colors;
    expect(a).toEqual(b);
  });

  it('ignores transparent pixels when building the palette', () => {
    const q = quantize(transparentArtwork(), 4);
    expect(q.transparentPixels).toBeGreaterThan(0);
    // Only the green disc is opaque, so one colour dominates completely.
    expect(q.colors[0].share).toBeGreaterThan(0.9);
  });
});

describe('artwork analysis', () => {
  it('detects a solid background and excludes it from the foreground', () => {
    const analysis = analyzeArtwork(simpleSquare(), options());
    expect(analysis.backgroundDetected).toBe(true);
    expect(analysis.backgroundColorIndex).not.toBeNull();
    expect(analysis.foregroundRegions.length).toBeGreaterThan(0);
    // The background colour must not appear as something to stitch.
    for (const region of analysis.foregroundRegions) {
      expect(region.colorIndex).not.toBe(analysis.backgroundColorIndex);
    }
  });

  it('rates a flat logo as well suited and a gradient as poorly suited', () => {
    const logo = analyzeArtwork(simpleSquare(), options());
    const gradient = analyzeArtwork(gradientArtwork(), options());
    expect(logo.embroiderySuitabilityScore).toBeGreaterThan(gradient.embroiderySuitabilityScore);
    expect(gradient.gradientScore).toBeGreaterThan(logo.gradientScore);
    expect(gradient.warnings.some((w) => w.code === 'artwork.gradients')).toBe(true);
  });

  it('classifies a long thin bar as a satin column', () => {
    const analysis = analyzeArtwork(thinBarArtwork(), options(120, 36));
    const bar = analysis.foregroundRegions.find((r) => r.areaShare > 0.05);
    expect(bar).toBeDefined();
    expect(bar!.suggestedStitchType).toBe('satin');
  });

  it('reports transparency', () => {
    const analysis = analyzeArtwork(transparentArtwork(), options());
    expect(analysis.hasTransparency).toBe(true);
    expect(analysis.transparentPixelShare).toBeGreaterThan(0.2);
  });

  it('warns rather than inventing regions for blank artwork', () => {
    const analysis = analyzeArtwork(blankArtwork(), options());
    expect(analysis.foregroundRegions.length).toBe(0);
    expect(analysis.warnings.some((w) => w.code === 'artwork.empty')).toBe(true);
  });

  it('never claims to have detected text', () => {
    const analysis = analyzeArtwork(threeColorArtwork(), options());
    expect(analysis.textRegions.detected).toBe(false);
    expect(analysis.textRegions.note).toMatch(/not implemented/i);
  });

  it('finds the hole in a ring', () => {
    const analysis = analyzeArtwork(ringArtwork(), options());
    // The white centre is a separate region from the white surround.
    expect(analysis.foregroundRegions.length).toBeGreaterThanOrEqual(1);
    expect(analysis.detectedColors.length).toBeGreaterThanOrEqual(2);
  });
});
