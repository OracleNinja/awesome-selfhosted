/**
 * Browser adapter: file -> RasterImage.
 *
 * This is the only place in the artwork path that touches the DOM. Everything
 * downstream works on the plain pixel buffer produced here, which is why the
 * pipeline can be tested outside a browser.
 */

import type { RasterImage } from '../processing/image/raster';

export const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/svg+xml'] as const;
export const ACCEPTED_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.svg'] as const;

export interface DecodedArtwork {
  image: RasterImage;
  fileName: string;
  mimeType: string;
  byteLength: number;
  /** Original bytes, base64 encoded. Kept verbatim and never modified. */
  base64: string;
  /** Natural pixel size of the source (for SVG, the rasterised size). */
  width: number;
  height: number;
  isVector: boolean;
  hasTransparency: boolean;
}

export class UnsupportedFileError extends Error {}

/** Longest side an SVG is rasterised to before analysis. */
const SVG_RASTER_SIZE = 1024;

export async function decodeArtworkFile(file: File): Promise<DecodedArtwork> {
  const name = file.name.toLowerCase();
  const extensionOk = ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
  const typeOk = (ACCEPTED_TYPES as readonly string[]).includes(file.type);

  if (!extensionOk && !typeOk) {
    throw new UnsupportedFileError(
      `"${file.name}" is not a supported artwork file. Upload a PNG, JPG, JPEG or SVG.`,
    );
  }

  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  const base64 = bytesToBase64(bytes);
  const isVector = name.endsWith('.svg') || file.type === 'image/svg+xml';

  const url = URL.createObjectURL(file);
  try {
    const bitmap = await loadImage(url, file.name);
    // An SVG has no intrinsic pixel size worth trusting; rasterise it big
    // enough that thin strokes survive the analysis step.
    let width = bitmap.naturalWidth || bitmap.width;
    let height = bitmap.naturalHeight || bitmap.height;
    if (isVector || width === 0 || height === 0) {
      const aspect = width && height ? width / height : 1;
      if (aspect >= 1) {
        width = SVG_RASTER_SIZE;
        height = Math.max(1, Math.round(SVG_RASTER_SIZE / aspect));
      } else {
        height = SVG_RASTER_SIZE;
        width = Math.max(1, Math.round(SVG_RASTER_SIZE * aspect));
      }
    }

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('This browser did not provide a 2D canvas context, so artwork cannot be read.');
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(bitmap, 0, 0, width, height);

    const data = ctx.getImageData(0, 0, width, height);
    const image: RasterImage = {
      width,
      height,
      data: new Uint8ClampedArray(data.data),
    };

    let transparent = false;
    for (let i = 3; i < image.data.length; i += 4) {
      if (image.data[i] < 255) {
        transparent = true;
        break;
      }
    }

    return {
      image,
      fileName: file.name,
      mimeType: file.type || (isVector ? 'image/svg+xml' : 'image/png'),
      byteLength: bytes.length,
      base64,
      width,
      height,
      isVector,
      hasTransparency: transparent,
    };
  } finally {
    URL.revokeObjectURL(url);
  }
}

function loadImage(url: string, fileName: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () =>
      reject(
        new UnsupportedFileError(
          `"${fileName}" could not be decoded as an image. The file may be corrupt or use an unsupported encoding.`,
        ),
      );
    img.src = url;
  });
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/** Rebuild a RasterImage from stored project bytes. */
export async function decodeStoredArtwork(
  base64: string,
  mimeType: string,
  fileName: string,
): Promise<DecodedArtwork> {
  const bytes = base64ToBytes(base64);
  const blob = new Blob([bytes as unknown as BlobPart], { type: mimeType });
  const file = new File([blob], fileName, { type: mimeType });
  return decodeArtworkFile(file);
}
