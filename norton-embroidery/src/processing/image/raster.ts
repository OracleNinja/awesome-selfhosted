/**
 * RasterImage is the DOM-free image representation the whole processing
 * pipeline works on. The browser decodes files into one of these via a thin
 * canvas adapter (see `src/app/image-decode.ts`); tests build them directly.
 */

export interface RasterImage {
  width: number;
  height: number;
  /** RGBA, 4 bytes per pixel, row-major. */
  data: Uint8ClampedArray;
}

export function createImage(width: number, height: number, fill = 0): RasterImage {
  const data = new Uint8ClampedArray(width * height * 4);
  if (fill !== 0) data.fill(fill);
  return { width, height, data };
}

export function getPixel(img: RasterImage, x: number, y: number): [number, number, number, number] {
  const i = (y * img.width + x) * 4;
  return [img.data[i], img.data[i + 1], img.data[i + 2], img.data[i + 3]];
}

export function setPixel(
  img: RasterImage,
  x: number,
  y: number,
  r: number,
  g: number,
  b: number,
  a = 255,
): void {
  const i = (y * img.width + x) * 4;
  img.data[i] = r;
  img.data[i + 1] = g;
  img.data[i + 2] = b;
  img.data[i + 3] = a;
}

export function hasAlpha(img: RasterImage): boolean {
  for (let i = 3; i < img.data.length; i += 4) if (img.data[i] < 255) return true;
  return false;
}

export function fullyTransparentPixels(img: RasterImage): number {
  let n = 0;
  for (let i = 3; i < img.data.length; i += 4) if (img.data[i] === 0) n++;
  return n;
}

/** Box-filter resize. Adequate for analysis; never applied to the stored original. */
export function resize(img: RasterImage, width: number, height: number): RasterImage {
  if (width === img.width && height === img.height) {
    return { width, height, data: new Uint8ClampedArray(img.data) };
  }
  const out = createImage(width, height);
  const sx = img.width / width;
  const sy = img.height / height;
  for (let y = 0; y < height; y++) {
    const y0 = Math.floor(y * sy);
    const y1 = Math.max(y0 + 1, Math.floor((y + 1) * sy));
    for (let x = 0; x < width; x++) {
      const x0 = Math.floor(x * sx);
      const x1 = Math.max(x0 + 1, Math.floor((x + 1) * sx));
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      let n = 0;
      for (let yy = y0; yy < Math.min(y1, img.height); yy++) {
        for (let xx = x0; xx < Math.min(x1, img.width); xx++) {
          const i = (yy * img.width + xx) * 4;
          const alpha = img.data[i + 3];
          // Weight colour by alpha so transparent pixels do not darken edges.
          r += img.data[i] * alpha;
          g += img.data[i + 1] * alpha;
          b += img.data[i + 2] * alpha;
          a += alpha;
          n++;
        }
      }
      if (n === 0) continue;
      const outIndex = (y * width + x) * 4;
      const alphaAvg = a / n;
      if (a > 0) {
        out.data[outIndex] = r / a;
        out.data[outIndex + 1] = g / a;
        out.data[outIndex + 2] = b / a;
      }
      out.data[outIndex + 3] = alphaAvg;
    }
  }
  return out;
}

/** Scale an image down so its longest side is at most `maxSide`. */
export function fitWithin(img: RasterImage, maxSide: number): RasterImage {
  const longest = Math.max(img.width, img.height);
  if (longest <= maxSide) return img;
  const scale = maxSide / longest;
  return resize(img, Math.max(1, Math.round(img.width * scale)), Math.max(1, Math.round(img.height * scale)));
}

export function toGrayscale(img: RasterImage): Float32Array {
  const out = new Float32Array(img.width * img.height);
  for (let i = 0, p = 0; i < img.data.length; i += 4, p++) {
    out[p] = 0.299 * img.data[i] + 0.587 * img.data[i + 1] + 0.114 * img.data[i + 2];
  }
  return out;
}

/** Sobel gradient magnitude, normalised to 0..1. */
export function sobelMagnitude(img: RasterImage): Float32Array {
  const { width: w, height: h } = img;
  const gray = toGrayscale(img);
  const out = new Float32Array(w * h);
  let max = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const tl = gray[i - w - 1];
      const t = gray[i - w];
      const tr = gray[i - w + 1];
      const l = gray[i - 1];
      const r = gray[i + 1];
      const bl = gray[i + w - 1];
      const b = gray[i + w];
      const br = gray[i + w + 1];
      const gx = tl + 2 * l + bl - (tr + 2 * r + br);
      const gy = tl + 2 * t + tr - (bl + 2 * b + br);
      const m = Math.hypot(gx, gy);
      out[i] = m;
      if (m > max) max = m;
    }
  }
  if (max > 0) for (let i = 0; i < out.length; i++) out[i] /= max;
  return out;
}

/**
 * Chamfer distance transform of a boolean mask: for each `true` pixel, the
 * approximate distance in pixels to the nearest `false` pixel. Used to measure
 * how wide a region is, which decides fill vs satin vs running stitch.
 */
export function distanceTransform(mask: Uint8Array, w: number, h: number): Float32Array {
  const INF = 1e9;
  const d = new Float32Array(w * h);
  for (let i = 0; i < d.length; i++) d[i] = mask[i] ? INF : 0;

  const at = (x: number, y: number): number => (x < 0 || y < 0 || x >= w || y >= h ? 0 : d[y * w + x]);

  // Forward pass.
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (d[i] === 0) continue;
      d[i] = Math.min(
        d[i],
        at(x - 1, y) + 1,
        at(x, y - 1) + 1,
        at(x - 1, y - 1) + Math.SQRT2,
        at(x + 1, y - 1) + Math.SQRT2,
      );
    }
  }
  // Backward pass.
  for (let y = h - 1; y >= 0; y--) {
    for (let x = w - 1; x >= 0; x--) {
      const i = y * w + x;
      if (d[i] === 0) continue;
      d[i] = Math.min(
        d[i],
        at(x + 1, y) + 1,
        at(x, y + 1) + 1,
        at(x + 1, y + 1) + Math.SQRT2,
        at(x - 1, y + 1) + Math.SQRT2,
      );
    }
  }
  return d;
}
