/**
 * PES version 1 writer.
 *
 * PES v1 is the most widely readable variant: Brother machines from the PE-Design
 * era onward accept it, as do the SE-series machines, and third-party software
 * (Ink/Stitch, Embrilliance, pyembroidery, libembroidery) parses it.
 *
 * File layout:
 *   "#PES0001" | uint32 offset of the PEC block | CEmbOne/CSewSeg vector
 *   section | PEC block (see pec.ts)
 *
 * The CSewSeg section stores the stitches a second time in absolute
 * coordinates. Machines read the PEC block; design software reads CSewSeg.
 * Both are written so the file works everywhere.
 */

import { BinaryWriter } from './binary';
import { StitchCommand, type Stitch } from '../../domain/stitch';
import type { Thread } from '../../domain/thread';
import { nearestPecIndex, stitchBounds, writePec } from './pec';

const PES_V1_SIGNATURE = '#PES0001';
const EMB_ONE = 'CEmbOne';
const EMB_SEG = 'CSewSeg';

export interface PesWriteOptions {
  /** Design name, truncated to 8 characters in the PEC header. */
  name: string;
}

interface Segment {
  points: Array<{ x: number; y: number }>;
  colorCode: number;
  /** 0 = stitched run, 1 = jump. */
  flag: number;
}

/**
 * Group the stitch stream into CSewSeg segments: contiguous runs of the same
 * command, with colour changes advancing the thread.
 */
function buildSegments(
  stitches: readonly Stitch[],
  threads: readonly Thread[],
  adjustX: number,
  adjustY: number,
): Segment[] {
  const segments: Segment[] = [];
  let colorIndex = 0;
  let colorCode = threads.length ? nearestPecIndex(threads[0]) : 1;
  colorIndex++;

  let stitchedX = 0;
  let stitchedY = 0;

  let i = 0;
  while (i < stitches.length) {
    const command = stitches[i].command;
    let j = i;
    while (j < stitches.length && stitches[j].command === command) j++;
    const block = stitches.slice(i, j);
    i = j;

    if (command === StitchCommand.Jump) {
      const lastPos = block[block.length - 1];
      segments.push({
        points: [
          { x: stitchedX - adjustX, y: stitchedY - adjustY },
          { x: lastPos.x - adjustX, y: lastPos.y - adjustY },
        ],
        colorCode,
        flag: 1,
      });
    } else if (command === StitchCommand.ColorChange) {
      const next = threads[colorIndex];
      if (next) colorCode = nearestPecIndex(next);
      colorIndex++;
    } else if (command === StitchCommand.Stitch) {
      const points = block.map((s) => {
        stitchedX = s.x;
        stitchedY = s.y;
        return { x: s.x - adjustX, y: s.y - adjustY };
      });
      segments.push({ points, colorCode, flag: 0 });
    }
    // End and anything else contribute no segment.
  }

  return segments;
}

/**
 * Encode a design as a PES v1 file.
 *
 * @throws RangeError when the stitch stream cannot be represented (for example
 *         more colour blocks than the format's 256 limit).
 */
export function writePes(
  stitches: readonly Stitch[],
  threads: readonly Thread[],
  options: PesWriteOptions,
): Uint8Array {
  const w = new BinaryWriter(1 << 16);
  w.ascii(PES_V1_SIGNATURE);
  const pecOffsetPlaceholder = w.tell();
  w.u32(0);

  const bounds = stitchBounds(stitches);
  const cx = (bounds.maxX + bounds.minX) / 2;
  const cy = (bounds.maxY + bounds.minY) / 2;
  const left = bounds.minX - cx;
  const top = bounds.minY - cy;
  const right = bounds.maxX - cx;
  const bottom = bounds.maxY - cy;

  if (stitches.length === 0) {
    writeHeaderV1(w, 0);
    w.u16(0x0000);
    w.u16(0x0000);
  } else {
    writeHeaderV1(w, 1);
    w.u16(0xffff);
    w.u16(0x0000);
    writeBlocks(w, stitches, threads, left, top, right, bottom, cx, cy);
  }

  w.patchU32(pecOffsetPlaceholder, w.tell());
  writePec(w, stitches, threads, options.name);

  return w.toUint8Array();
}

function writeHeaderV1(w: BinaryWriter, distinctBlockObjects: number): void {
  w.u16(0x01); // scale to fit
  w.u16(0x01); // hoop selector
  w.u16(distinctBlockObjects);
}

function writeBlocks(
  w: BinaryWriter,
  stitches: readonly Stitch[],
  threads: readonly Thread[],
  left: number,
  top: number,
  right: number,
  bottom: number,
  cx: number,
  cy: number,
): void {
  w.pesString16(EMB_ONE);
  const sectionCountPlaceholder = writeSewSegHeader(w, left, top, right, bottom);
  w.u16(0xffff);
  w.u16(0x0000); // FFFF 0000 => another block follows

  w.pesString16(EMB_SEG);

  // Segment coordinates are relative to the design's top-left corner.
  const adjustX = left + cx;
  const adjustY = bottom + cy;
  const segments = buildSegments(stitches, threads, adjustX, adjustY);

  const colorLog: Array<[number, number]> = [];
  let previousColorCode = -1;
  let first = true;

  segments.forEach((segment, index) => {
    if (!first) w.u16(0x8003); // end of the previous section
    first = false;

    if (previousColorCode !== segment.colorCode) {
      colorLog.push([index, segment.colorCode]);
      previousColorCode = segment.colorCode;
    }

    w.u16(segment.flag);
    w.u16(segment.colorCode);
    w.u16(segment.points.length);
    for (const p of segment.points) {
      w.u16(Math.round(p.x));
      w.u16(Math.round(p.y));
    }
  });

  w.u16(colorLog.length);
  for (const [section, code] of colorLog) {
    w.u16(section);
    w.u16(code);
  }

  w.patchU16(sectionCountPlaceholder, segments.length);

  w.u16(0x0000);
  w.u16(0x0000); // 0000 0000 => no further blocks
}

/** Returns the offset of the section-count placeholder for later patching. */
function writeSewSegHeader(
  w: BinaryWriter,
  left: number,
  top: number,
  right: number,
  bottom: number,
): number {
  const width = right - left;
  const height = bottom - top;
  const hoopHeight = 1800;
  const hoopWidth = 1300;

  for (let i = 0; i < 8; i++) w.u16(0);

  const transX = 350 + hoopWidth / 2 - width / 2;
  const transY = 100 + height + hoopHeight / 2 - height / 2;

  w.f32(1).f32(0).f32(0).f32(1).f32(transX).f32(transY);

  w.u16(1);
  w.u16(0);
  w.u16(0);
  w.u16(Math.trunc(width));
  w.u16(Math.trunc(height));
  w.bytes([0, 0, 0, 0, 0, 0, 0, 0]);

  const placeholder = w.tell();
  w.u16(0);
  return placeholder;
}
