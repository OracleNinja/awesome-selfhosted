/**
 * PES / PEC reader.
 *
 * Both formats are read through their PEC stitch block, which every PES version
 * carries. That gives stitches, colour sequence and dimensions for any PES file
 * regardless of version.
 *
 * Limitation, stated plainly: PES stores the original shapes only in the
 * version-specific vector section, which this reader does not parse. An
 * imported design therefore arrives as stitches without editable objects. It
 * can be viewed, simulated, validated and re-exported, but shape-level editing
 * (changing a fill angle, re-running density) is not possible on imported
 * files.
 */

import { BinaryReader } from './binary';
import { StitchCommand, type Stitch } from '../../domain/stitch';
import { PEC_THREADS, type Thread } from '../../domain/thread';

const FLAG_LONG = 0x80;
const JUMP_CODE = 0x10;
const TRIM_CODE = 0x20;

export interface ImportedDesign {
  stitches: Stitch[];
  threads: Thread[];
  name: string;
  /** Format actually detected in the file. */
  format: 'PES' | 'PEC';
  /** PES version number when the file declares one. */
  version: number | null;
  /** Anything the reader could not fully interpret. */
  limitations: string[];
}

function signed7(b: number): number {
  return b > 63 ? b - 128 : b;
}

function signed12(code: number): number {
  const b = code & 0xfff;
  return b > 0x7ff ? b - 0x1000 : b;
}

export function readPes(bytes: Uint8Array): ImportedDesign {
  if (bytes.length < 12) throw new Error('File is too small to be a PES or PEC file.');
  const r = new BinaryReader(bytes);
  const signature = r.ascii(8);

  if (signature === '#PEC0001') {
    const result = readPecBlock(bytes, 8);
    return { ...result, format: 'PEC', version: null };
  }

  if (!signature.startsWith('#PES')) {
    throw new Error(
      `Not a PES file: expected a "#PES" or "#PEC" signature, found "${sanitize(signature)}".`,
    );
  }

  const version = parseVersion(signature);
  const pecOffset = r.u32();
  if (pecOffset < 12 || pecOffset >= bytes.length) {
    throw new Error(
      `PES file declares its stitch block at byte ${pecOffset}, which is outside the ${bytes.length}-byte file. The file is truncated or corrupt.`,
    );
  }

  const result = readPecBlock(bytes, pecOffset);
  return {
    ...result,
    format: 'PES',
    version,
    limitations: [
      ...result.limitations,
      'Imported from the stitch block. Original shapes are not recovered, so objects cannot be re-digitized — only viewed, validated and re-exported.',
    ],
  };
}

function parseVersion(signature: string): number | null {
  const digits = signature.slice(4);
  const n = Number(digits);
  return Number.isFinite(n) ? n / 10 : null;
}

function readPecBlock(
  bytes: Uint8Array,
  start: number,
): Omit<ImportedDesign, 'format' | 'version'> {
  const r = new BinaryReader(bytes);
  r.seek(start);

  r.skip(3); // "LA:"
  const name = r.ascii(16).trim();
  r.skip(0x0f); // spaces, then 0xFF 0x00
  const stride = r.u8();
  const iconHeight = r.u8();
  r.skip(0x0c);

  const colorChanges = r.u8();
  const colorCount = colorChanges + 1;
  const paletteBytes: number[] = [];
  for (let i = 0; i < colorCount; i++) paletteBytes.push(r.u8());

  const limitations: string[] = [];
  const threads: Thread[] = paletteBytes.map((b) => {
    // Palette index 0 is reserved; the format wraps indices modulo the chart.
    const index = b % (PEC_THREADS.length + 1);
    const thread = index === 0 ? PEC_THREADS[PEC_THREADS.length - 1] : PEC_THREADS[index - 1];
    return thread;
  });

  r.skip(0x1d0 - colorChanges);
  r.u24(); // stitch block length, not needed for decoding
  r.skip(0x0b);

  const stitches = decodeStitches(r);

  if (stride !== 6 || iconHeight !== 38) {
    limitations.push(
      `Unusual thumbnail geometry (${stride} byte stride, ${iconHeight} rows). Stitches were read anyway.`,
    );
  }
  if (stitches.length === 0) {
    limitations.push('The stitch block decoded to zero stitches.');
  }

  return { stitches, threads, name: name || 'Imported design', limitations };
}

function decodeStitches(r: BinaryReader): Stitch[] {
  const out: Stitch[] = [];
  let x = 0;
  let y = 0;

  for (;;) {
    if (r.remaining < 2) break;
    const val1 = r.u8();
    let val2 = r.u8();

    if (val1 === 0xff && val2 === 0x00) break;
    if (val1 === 0xff) {
      // A lone 0xFF also terminates the block.
      break;
    }
    if (val1 === 0xfe && val2 === 0xb0) {
      if (r.remaining < 1) break;
      r.skip(1);
      out.push({ x, y, command: StitchCommand.ColorChange });
      continue;
    }

    let jump = false;
    let trim = false;
    let dx: number;
    let dy: number;

    if ((val1 & FLAG_LONG) !== 0) {
      if ((val1 & TRIM_CODE) !== 0) trim = true;
      if ((val1 & JUMP_CODE) !== 0) jump = true;
      dx = signed12((val1 << 8) | val2);
      if (r.remaining < 1) break;
      val2 = r.u8();
    } else {
      dx = signed7(val1);
    }

    if ((val2 & FLAG_LONG) !== 0) {
      if ((val2 & TRIM_CODE) !== 0) trim = true;
      if ((val2 & JUMP_CODE) !== 0) jump = true;
      if (r.remaining < 1) break;
      const val3 = r.u8();
      dy = signed12((val2 << 8) | val3);
    } else {
      dy = signed7(val2);
    }

    x += dx;
    y += dy;
    out.push({ x, y, command: jump || trim ? StitchCommand.Jump : StitchCommand.Stitch });
  }

  const last = out[out.length - 1];
  out.push({ x: last?.x ?? 0, y: last?.y ?? 0, command: StitchCommand.End });
  return out;
}

function sanitize(s: string): string {
  return s.replace(/[^\x20-\x7e]/g, '?');
}
