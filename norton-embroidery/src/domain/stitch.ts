/** Stitch-level primitives. Coordinates are in 0.1 mm units, y-down. */

export enum StitchCommand {
  /** Needle penetrates the fabric at this point. */
  Stitch = 'STITCH',
  /**
   * Machine moves without stitching. In PEC/PES a jump that follows any
   * previous stitching is encoded as a trim-jump (thread is cut), which is why
   * trims are not a separate command in the stitch stream.
   */
  Jump = 'JUMP',
  /** Thread colour change. Consumes the next entry of the thread sequence. */
  ColorChange = 'COLOR_CHANGE',
  /** Terminates the design. Must be the final element. */
  End = 'END',
}

export interface Stitch {
  x: number;
  y: number;
  command: StitchCommand;
}

export function stitch(x: number, y: number): Stitch {
  return { x, y, command: StitchCommand.Stitch };
}

export function jump(x: number, y: number): Stitch {
  return { x, y, command: StitchCommand.Jump };
}

export function colorChange(x: number, y: number): Stitch {
  return { x, y, command: StitchCommand.ColorChange };
}

export function end(x: number, y: number): Stitch {
  return { x, y, command: StitchCommand.End };
}

/** A run of stitches sharing one thread. */
export interface ColorBlock {
  threadIndex: number;
  /** Index into the stitch array where this block starts (inclusive). */
  start: number;
  /** Index into the stitch array where this block ends (exclusive). */
  endExclusive: number;
  stitchCount: number;
}

/** Split a stitch stream into colour blocks. */
export function colorBlocks(stitches: readonly Stitch[]): ColorBlock[] {
  const blocks: ColorBlock[] = [];
  let threadIndex = 0;
  let start = 0;
  let count = 0;
  for (let i = 0; i < stitches.length; i++) {
    const s = stitches[i];
    if (s.command === StitchCommand.Stitch) count++;
    if (s.command === StitchCommand.ColorChange) {
      blocks.push({ threadIndex, start, endExclusive: i, stitchCount: count });
      threadIndex++;
      start = i + 1;
      count = 0;
    } else if (s.command === StitchCommand.End) {
      blocks.push({ threadIndex, start, endExclusive: i, stitchCount: count });
      return blocks;
    }
  }
  blocks.push({ threadIndex, start, endExclusive: stitches.length, stitchCount: count });
  return blocks;
}

export function countCommand(stitches: readonly Stitch[], command: StitchCommand): number {
  let n = 0;
  for (const s of stitches) if (s.command === command) n++;
  return n;
}

/**
 * Number of thread trims. In PEC the first jump of the design positions the
 * needle and does not trim; every later jump does.
 */
export function countTrims(stitches: readonly Stitch[]): number {
  let seenStitch = false;
  let trims = 0;
  for (const s of stitches) {
    if (s.command === StitchCommand.Stitch) seenStitch = true;
    else if (s.command === StitchCommand.Jump && seenStitch) trims++;
  }
  return trims;
}

/** Total length of thread actually laid down, in 0.1 mm units. */
export function totalStitchLength(stitches: readonly Stitch[]): number {
  let total = 0;
  let prev: Stitch | null = null;
  for (const s of stitches) {
    if (s.command === StitchCommand.Stitch && prev && prev.command === StitchCommand.Stitch) {
      total += Math.hypot(s.x - prev.x, s.y - prev.y);
    }
    prev = s;
  }
  return total;
}
