/**
 * StitchSequence: the one artifact that both the preview and the exported file
 * are built from.
 *
 * The preview and the PES encoder used to each resolve threads for themselves.
 * They agreed in the simple case and disagreed in a real one: a colour block's
 * ordinal is not a palette index, so a design that leaves a colour and returns
 * to it (which layering forces) mapped blocks onto the wrong threads. Resolving
 * that once, here, is what makes "what you see is what gets sewn" a property of
 * the code rather than a coincidence.
 *
 * The `id` is a content digest over the exact stitches and threads. The UI shows
 * it next to both the preview and the exported file so an operator can see they
 * came from the same data.
 */

import { computeStats, type DesignStats, type EmbroideryDesign } from './design';
import { colorBlocks, StitchCommand, type ColorBlock, type Stitch } from './stitch';
import { threadHex, type Thread } from './thread';

export interface StitchSequence {
  /** The exact stitch stream that is rendered and encoded. */
  readonly stitches: readonly Stitch[];
  /** Colour blocks of that stream, in sew order. */
  readonly blocks: readonly ColorBlock[];
  /**
   * The thread for each colour block, in sew order. Index by the block's
   * position in `blocks`, never by a palette index.
   */
  readonly blockThreads: readonly Thread[];
  /** Palette index used by each block; the same cone may appear twice. */
  readonly blockPaletteIndices: readonly number[];
  /** Distinct physical thread cones the operator has to load. */
  readonly distinctThreads: readonly Thread[];
  /** Content digest of the stitches and threads above. */
  readonly id: string;
  readonly stats: DesignStats;
  /** True when a colour is left and later returned to. */
  readonly hasRepeatedColors: boolean;
}

/**
 * Resolve a design into the sequence that will be previewed and exported.
 *
 * `design.colorSequence` records which palette entry each block uses and is
 * written by stitch generation. Designs imported from a stitch file have no
 * objects to generate from, so their palette is already one entry per block and
 * the identity mapping is used instead.
 */
export function buildStitchSequence(design: EmbroideryDesign): StitchSequence {
  const blocks = colorBlocks(design.stitches);
  const palette = design.threadPalette;

  const blockPaletteIndices = blocks.map((block, i) => {
    const recorded = design.colorSequence?.[i];
    if (recorded !== undefined && palette[recorded]) return recorded;
    // Imported designs, and any older project file, map block ordinal to
    // palette entry one to one.
    if (palette[block.threadIndex]) return block.threadIndex;
    return 0;
  });

  const blockThreads = blockPaletteIndices.map((index) => palette[index]).filter(Boolean);

  const distinct: Thread[] = [];
  for (const t of blockThreads) {
    if (!distinct.some((d) => d.id === t.id)) distinct.push(t);
  }

  return {
    stitches: design.stitches,
    blocks,
    blockThreads,
    blockPaletteIndices,
    distinctThreads: distinct,
    id: sequenceDigest(design.stitches, blockThreads),
    stats: computeStats(design),
    hasRepeatedColors: distinct.length < blockThreads.length,
  };
}

/**
 * Content digest of a stitch stream and its threads.
 *
 * Two 32-bit FNV-1a accumulators with different seeds, printed as 16 hex
 * characters. This is an integrity check for humans comparing a preview to a
 * file, not a security primitive, and it is synchronous so the UI can show it
 * without an await.
 */
export function sequenceDigest(
  stitches: readonly Stitch[],
  threads: readonly Thread[],
): string {
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;

  const mix = (value: number): void => {
    h1 = Math.imul(h1 ^ (value & 0xff), 0x01000193) >>> 0;
    h1 = Math.imul(h1 ^ ((value >>> 8) & 0xff), 0x01000193) >>> 0;
    h2 = Math.imul(h2 ^ ((value >>> 16) & 0xff), 0x85ebca6b) >>> 0;
    h2 = Math.imul(h2 ^ ((value >>> 24) & 0xff), 0x85ebca6b) >>> 0;
  };

  const mixString = (s: string): void => {
    for (let i = 0; i < s.length; i++) mix(s.charCodeAt(i));
  };

  mix(stitches.length);
  for (const s of stitches) {
    mix(COMMAND_CODE[s.command]);
    // Coordinates are rounded exactly as the encoder rounds them, so the digest
    // reflects what actually reaches the file.
    mix(Math.round(s.x) | 0);
    mix(Math.round(s.y) | 0);
  }

  mix(threads.length);
  for (const t of threads) {
    mixString(t.id);
    mix((t.r << 16) | (t.g << 8) | t.b);
  }

  return (h1 >>> 0).toString(16).padStart(8, '0') + (h2 >>> 0).toString(16).padStart(8, '0');
}

const COMMAND_CODE: Record<StitchCommand, number> = {
  [StitchCommand.Stitch]: 1,
  [StitchCommand.Jump]: 2,
  [StitchCommand.ColorChange]: 3,
  [StitchCommand.End]: 4,
};

/** Short form of the digest, for display next to a preview. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

/**
 * The colour-change list an operator works through at the machine: one row per
 * colour block, in sew order, with the stitches sewn in that block.
 */
export interface ColorStop {
  /** 1-based position in the sew order. */
  step: number;
  thread: Thread;
  hex: string;
  stitchCount: number;
  /** True when this cone was already loaded earlier in the design. */
  repeatOfStep: number | null;
}

export function colorStops(sequence: StitchSequence): ColorStop[] {
  const seen = new Map<string, number>();
  return sequence.blockThreads.map((thread, i) => {
    const previous = seen.get(thread.id) ?? null;
    if (previous === null) seen.set(thread.id, i + 1);
    return {
      step: i + 1,
      thread,
      hex: threadHex(thread),
      stitchCount: sequence.blocks[i]?.stitchCount ?? 0,
      repeatOfStep: previous,
    };
  });
}
