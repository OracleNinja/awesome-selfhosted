/**
 * PEC block encoder.
 *
 * PEC is the stitch container that Brother machines actually read; a PES file
 * is a wrapper around one. Layout implemented here:
 *
 *   0x000  "LA:" + 16-char name + CR
 *   0x014  12 spaces, 0xFF, 0x00
 *   0x022  thumbnail byte-stride (6) and height (38)
 *   0x024  12 spaces, colour-count-1, then one palette index per colour block,
 *          padded with spaces to 0x200
 *   0x200  stitch block: header, then variable-length encoded stitches
 *   ...    one 48x38 1-bit thumbnail for the whole design, then one per colour
 *
 * Verified by round-tripping through pyembroidery in the test-suite.
 */

import { BinaryWriter } from './binary';
import { StitchCommand, colorBlocks, type Stitch } from '../../domain/stitch';
import { PEC_THREADS, colorDistance, type Thread } from '../../domain/thread';

const MASK_07_BIT = 0b0111_1111;
const JUMP_CODE = 0b0001_0000;
const TRIM_CODE = 0b0010_0000;

export const PEC_ICON_WIDTH = 48;
export const PEC_ICON_HEIGHT = 38;
const ICON_STRIDE = PEC_ICON_WIDTH / 8; // 6 bytes per row

/**
 * The empty thumbnail: a 48x38 bitmap pre-drawn with a rectangular frame.
 * Brother's own files carry this frame, so designs look right in the machine's
 * design browser.
 */
function blankIcon(): Uint8Array {
  const g = new Uint8Array(ICON_STRIDE * PEC_ICON_HEIGHT);
  const setBit = (x: number, y: number): void => {
    g[y * ICON_STRIDE + (x >> 3)] |= 1 << (x % 8);
  };
  // Top and bottom edges span x = 4..43.
  for (let x = 4; x < PEC_ICON_WIDTH - 4; x++) {
    setBit(x, 1);
    setBit(x, PEC_ICON_HEIGHT - 2);
  }
  // Side edges.
  for (let y = 2; y < PEC_ICON_HEIGHT - 2; y++) {
    setBit(3, y);
    setBit(PEC_ICON_WIDTH - 4, y);
  }
  // Rounded corners.
  setBit(2, 2);
  setBit(PEC_ICON_WIDTH - 3, 2);
  setBit(2, PEC_ICON_HEIGHT - 3);
  setBit(PEC_ICON_WIDTH - 3, PEC_ICON_HEIGHT - 3);
  return g;
}

export interface PecBounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export function stitchBounds(stitches: readonly Stitch[]): PecBounds {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const s of stitches) {
    if (s.x < minX) minX = s.x;
    if (s.x > maxX) maxX = s.x;
    if (s.y < minY) minY = s.y;
    if (s.y > maxY) maxY = s.y;
  }
  if (!Number.isFinite(minX)) return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  return { minX, minY, maxX, maxY };
}

/**
 * Machine palette index (1..64) for each thread of the sequence.
 *
 * A palette entry is consumed once so two different design colours never
 * collapse onto the same machine slot, which is what makes the operator's
 * colour list on the machine match the design.
 */
export function buildUniquePalette(threads: readonly Thread[]): number[] {
  const available: Array<Thread | null> = [null, ...PEC_THREADS]; // index 0 unused
  const chart: Array<Thread | null> = new Array(available.length).fill(null);

  const seen = new Set<string>();
  const unique: Thread[] = [];
  for (const t of threads) {
    const key = `${t.r},${t.g},${t.b}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(t);
  }

  for (const t of unique) {
    const index = nearestAvailableIndex(t, available);
    if (index === null) break; // palette exhausted
    chart[index] = available[index];
    available[index] = null;
  }

  return threads.map((t) => {
    const index = nearestAvailableIndex(t, chart);
    return index ?? 1;
  });
}

function nearestAvailableIndex(t: Thread, pool: Array<Thread | null>): number | null {
  let best: number | null = null;
  let bestDist = Infinity;
  for (let i = 0; i < pool.length; i++) {
    const candidate = pool[i];
    if (!candidate) continue;
    const d = colorDistance(t.r, t.g, t.b, candidate.r, candidate.g, candidate.b);
    if (d <= bestDist) {
      bestDist = d;
      best = i;
    }
  }
  return best;
}

/** Palette index of the chart entry closest to a thread (never consumed). */
export function nearestPecIndex(t: Thread): number {
  const pool: Array<Thread | null> = [null, ...PEC_THREADS];
  return nearestAvailableIndex(t, pool) ?? 1;
}

function writeValue(w: BinaryWriter, value: number, long: boolean, flag = 0): void {
  if (!long && value > -64 && value < 63) {
    w.u8(value & MASK_07_BIT);
    return;
  }
  let v = value & 0b0000_1111_1111_1111;
  v |= 0b1000_0000_0000_0000;
  v |= flag << 8;
  w.u8((v >> 8) & 0xff);
  w.u8(v & 0xff);
}

/** Variable-length stitch encoding. Returns nothing; writes into `w`. */
export function pecEncode(w: BinaryWriter, stitches: readonly Stitch[]): void {
  let colorTwo = true;
  let jumping = true;
  let init = true;
  let xx = 0;
  let yy = 0;

  for (const s of stitches) {
    const dx = Math.round(s.x - xx);
    const dy = Math.round(s.y - yy);
    xx += dx;
    yy += dy;

    switch (s.command) {
      case StitchCommand.Stitch: {
        if (jumping) {
          if (dx !== 0 && dy !== 0) {
            writeValue(w, 0, false);
            writeValue(w, 0, false);
          }
          jumping = false;
        }
        writeValue(w, dx, false);
        writeValue(w, dy, false);
        break;
      }
      case StitchCommand.Jump: {
        jumping = true;
        // The very first move only positions the needle; later moves cut first.
        const flag = init ? JUMP_CODE : TRIM_CODE;
        writeValue(w, dx, true, flag);
        writeValue(w, dy, true, flag);
        break;
      }
      case StitchCommand.ColorChange: {
        if (jumping) {
          writeValue(w, 0, false);
          writeValue(w, 0, false);
          jumping = false;
        }
        w.u8(0xfe).u8(0xb0).u8(colorTwo ? 0x02 : 0x01);
        colorTwo = !colorTwo;
        break;
      }
      case StitchCommand.End: {
        w.u8(0xff);
        return;
      }
    }
    init = false;
  }
  w.u8(0xff);
}

function drawScaled(
  bounds: PecBounds,
  points: ReadonlyArray<{ x: number; y: number }>,
  graphic: Uint8Array,
  buffer: number,
): void {
  const diagramWidth = bounds.maxX - bounds.minX || 1;
  const diagramHeight = bounds.maxY - bounds.minY || 1;
  const graphicWidth = PEC_ICON_WIDTH;
  const graphicHeight = graphic.length / ICON_STRIDE;

  const scale = Math.min(
    (graphicWidth - buffer) / diagramWidth,
    (graphicHeight - buffer) / diagramHeight,
  );
  const cx = (bounds.maxX + bounds.minX) / 2;
  const cy = (bounds.maxY + bounds.minY) / 2;
  const tx = -cx * scale + graphicWidth / 2;
  const ty = -cy * scale + graphicHeight / 2;

  for (const p of points) {
    const x = Math.floor(p.x * scale + tx);
    const y = Math.floor(p.y * scale + ty);
    if (x < 0 || y < 0 || x >= graphicWidth || y >= graphicHeight) continue;
    graphic[y * ICON_STRIDE + (x >> 3)] |= 1 << (x % 8);
  }
}

export interface PecColorInfo {
  /** Byte written into the PEC colour table: count-1 followed by the indices. */
  colorIndexList: number[];
  /** 24-bit RGB per thread, in sequence order. */
  rgbList: number[];
}

/**
 * Write a complete PEC block (header + stitches + thumbnails) into `w`.
 * Returns the colour information the PES v6 addendum needs.
 */
export function writePec(
  w: BinaryWriter,
  stitches: readonly Stitch[],
  threads: readonly Thread[],
  name: string,
): PecColorInfo {
  const bounds = stitchBounds(stitches);

  // --- header -----------------------------------------------------------
  const shortName = name.slice(0, 8);
  w.ascii(`LA:${shortName.padEnd(16, ' ')}\r`);
  w.bytes([0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0xff, 0x00]);
  w.u8(ICON_STRIDE);
  w.u8(PEC_ICON_HEIGHT);

  const paletteIndices = buildUniquePalette(threads);
  const rgbList = threads.map((t) => ((t.r & 0xff) << 16) | ((t.g & 0xff) << 8) | (t.b & 0xff));
  const count = paletteIndices.length;

  if (count !== 0) {
    if (count - 1 > 255) {
      throw new RangeError(
        `PEC supports at most 256 colour blocks; this design has ${count}.`,
      );
    }
    w.bytes(new Array(12).fill(0x20));
    w.u8(count - 1);
    w.bytes(paletteIndices);
  } else {
    w.bytes([0x20, 0x20, 0x20, 0x20, 0x64, 0x20, 0x00, 0x20, 0x00, 0x20, 0x20, 0x20, 0xff]);
  }
  for (let i = count; i < 463; i++) w.u8(0x20);

  // --- stitch block -----------------------------------------------------
  const blockStart = w.tell();
  w.u16(0x0000);
  const lengthPlaceholder = w.tell();
  w.u24(0);
  w.bytes([0x31, 0xff, 0xf0]);
  w.u16(Math.round(bounds.maxX - bounds.minX));
  w.u16(Math.round(bounds.maxY - bounds.minY));
  w.u16(0x1e0);
  w.u16(0x1b0);
  pecEncode(w, stitches);
  w.patchU24(lengthPlaceholder, w.tell() - blockStart);

  // --- thumbnails -------------------------------------------------------
  const overall = blankIcon();
  drawScaled(bounds, stitchPointsOnly(stitches), overall, 4);
  w.bytes(overall);

  for (const block of colorBlocks(stitches)) {
    const icon = blankIcon();
    const pts = stitchPointsOnly(stitches.slice(block.start, block.endExclusive));
    drawScaled(bounds, pts, icon, 5);
    w.bytes(icon);
  }

  return { colorIndexList: [count - 1, ...paletteIndices], rgbList };
}

function stitchPointsOnly(stitches: readonly Stitch[]): Array<{ x: number; y: number }> {
  const out: Array<{ x: number; y: number }> = [];
  for (const s of stitches) if (s.command === StitchCommand.Stitch) out.push({ x: s.x, y: s.y });
  return out;
}
