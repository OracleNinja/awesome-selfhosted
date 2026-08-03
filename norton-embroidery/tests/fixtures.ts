/**
 * Test fixtures: small pieces of sample artwork built pixel by pixel, plus
 * helpers for encoding them as real PNG files so the upload path can be
 * exercised end to end.
 */

import { PNG } from 'pngjs';
import { createImage, setPixel, type RasterImage } from '../src/processing/image/raster';

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

export const RED: Rgb = { r: 220, g: 30, b: 40 };
export const BLUE: Rgb = { r: 20, g: 60, b: 180 };
export const GREEN: Rgb = { r: 30, g: 150, b: 70 };
export const WHITE: Rgb = { r: 255, g: 255, b: 255 };
export const BLACK: Rgb = { r: 0, g: 0, b: 0 };

export function fill(img: RasterImage, c: Rgb, a = 255): void {
  for (let y = 0; y < img.height; y++) {
    for (let x = 0; x < img.width; x++) setPixel(img, x, y, c.r, c.g, c.b, a);
  }
}

export function rect(
  img: RasterImage,
  x0: number,
  y0: number,
  w: number,
  h: number,
  c: Rgb,
  a = 255,
): void {
  for (let y = y0; y < y0 + h; y++) {
    for (let x = x0; x < x0 + w; x++) {
      if (x < 0 || y < 0 || x >= img.width || y >= img.height) continue;
      setPixel(img, x, y, c.r, c.g, c.b, a);
    }
  }
}

export function disc(img: RasterImage, cx: number, cy: number, r: number, c: Rgb, a = 255): void {
  for (let y = Math.floor(cy - r); y <= Math.ceil(cy + r); y++) {
    for (let x = Math.floor(cx - r); x <= Math.ceil(cx + r); x++) {
      if (x < 0 || y < 0 || x >= img.width || y >= img.height) continue;
      if ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) setPixel(img, x, y, c.r, c.g, c.b, a);
    }
  }
}

/** A single red square on a white background. The simplest possible logo. */
export function simpleSquare(size = 120): RasterImage {
  const img = createImage(size, size);
  fill(img, WHITE);
  rect(img, size * 0.2, size * 0.2, size * 0.6, size * 0.6, RED);
  return img;
}

/** A red disc with a white hole in the middle: exercises hole handling. */
export function ringArtwork(size = 140): RasterImage {
  const img = createImage(size, size);
  fill(img, WHITE);
  disc(img, size / 2, size / 2, size * 0.4, BLUE);
  disc(img, size / 2, size / 2, size * 0.18, WHITE);
  return img;
}

/** Three separate colour blocks: exercises multi-colour digitizing. */
export function threeColorArtwork(width = 180, height = 120): RasterImage {
  const img = createImage(width, height);
  fill(img, WHITE);
  rect(img, 10, 20, 45, 80, RED);
  rect(img, 68, 20, 45, 80, GREEN);
  rect(img, 126, 20, 45, 80, BLUE);
  return img;
}

/** A long thin bar: should be classified as a satin column. */
export function thinBarArtwork(width = 200, height = 60): RasterImage {
  const img = createImage(width, height);
  fill(img, WHITE);
  rect(img, 20, 25, 160, 10, BLACK);
  return img;
}

/** Transparent background with a shape: exercises alpha handling. */
export function transparentArtwork(size = 100): RasterImage {
  const img = createImage(size, size);
  // Leave everything at alpha 0, then draw an opaque disc.
  disc(img, size / 2, size / 2, size * 0.35, GREEN);
  return img;
}

/** Noisy gradient: should be reported as poorly suited to auto-digitizing. */
export function gradientArtwork(size = 120): RasterImage {
  const img = createImage(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      setPixel(img, x, y, (x * 255) / size, (y * 255) / size, ((x + y) * 127) / size, 255);
    }
  }
  return img;
}

/** Fully blank artwork: must be rejected downstream, not silently exported. */
export function blankArtwork(size = 64): RasterImage {
  const img = createImage(size, size);
  fill(img, WHITE);
  return img;
}

// --- PNG round-tripping ---------------------------------------------------

export function toPngBuffer(img: RasterImage): Buffer {
  const png = new PNG({ width: img.width, height: img.height });
  png.data = Buffer.from(img.data.buffer, img.data.byteOffset, img.data.byteLength);
  return PNG.sync.write(png);
}

export function fromPngBuffer(buffer: Buffer): RasterImage {
  const png = PNG.sync.read(buffer);
  return {
    width: png.width,
    height: png.height,
    data: new Uint8ClampedArray(png.data),
  };
}
