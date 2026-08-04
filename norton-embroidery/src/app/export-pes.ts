/**
 * Export workflow.
 *
 *   EmbroideryDesign
 *     -> stitch validation
 *     -> machine / hoop validation
 *     -> thread sequence validation
 *     -> PES encoder
 *     -> binary PES
 *     -> read the bytes back and compare against the source design
 *
 * The last step is what lets the application state that a file is valid. If the
 * re-read does not match the design, the export is reported as failed rather
 * than handed to the operator.
 */

import { computeStats, type EmbroideryDesign } from '../domain/design';
import type { MachineProfile } from '../domain/machine';
import { StitchCommand } from '../domain/stitch';
import { buildStitchSequence, type StitchSequence } from '../domain/stitch-sequence';
import type { ValidationReport } from '../domain/validation';
import { validateDesign } from '../processing/validate/validate-design';
import { writePes } from '../infra/pes/pes-writer';
import { readPes } from '../infra/pes/pes-reader';

export interface ExportCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ExportResult {
  ok: boolean;
  /** Present only when ok === true. */
  bytes?: Uint8Array;
  fileName: string;
  validation: ValidationReport;
  /** Checks run against the bytes that were actually produced. */
  verification: ExportCheck[];
  /** Reason the export was refused, when ok === false. */
  blockedReason?: string;
  /**
   * Digest of the StitchSequence that was encoded. The preview shows the same
   * digest, which is how a file can be tied to the picture it came from.
   */
  sequenceId: string;
}

export interface ExportOptions {
  /** The operator has read and accepted the warnings. */
  acknowledgeWarnings: boolean;
}

export function exportPes(
  design: EmbroideryDesign,
  machine: MachineProfile,
  options: ExportOptions = { acknowledgeWarnings: false },
): ExportResult {
  const validation = validateDesign(design, machine);
  const fileName = `${sanitizeFileName(design.metadata.name)}.pes`;

  // The exact same call the preview makes, so the file and the picture are
  // encoded from one artifact rather than two parallel resolutions.
  const sequence = buildStitchSequence(design);

  if (!validation.passed) {
    return {
      ok: false,
      fileName,
      validation,
      verification: [],
      sequenceId: sequence.id,
      blockedReason: `Export blocked: ${validation.errorCount} error(s) must be fixed first.`,
    };
  }

  if (validation.warningCount > 0 && !options.acknowledgeWarnings) {
    return {
      ok: false,
      fileName,
      validation,
      verification: [],
      sequenceId: sequence.id,
      blockedReason: `Export needs acknowledgement: ${validation.warningCount} warning(s) have not been accepted.`,
    };
  }

  // --- thread sequence --------------------------------------------------
  const threadsForFile = sequence.blockThreads;
  if (threadsForFile.length !== sequence.blocks.length) {
    return {
      ok: false,
      fileName,
      validation,
      verification: [],
      sequenceId: sequence.id,
      blockedReason: `Export blocked: the design has ${sequence.blocks.length} colour block(s) but only ${threadsForFile.length} could be matched to a thread.`,
    };
  }

  // --- encode -----------------------------------------------------------
  let bytes: Uint8Array;
  try {
    bytes = writePes(sequence.stitches, threadsForFile, { name: design.metadata.name });
  } catch (err) {
    return {
      ok: false,
      fileName,
      validation,
      verification: [],
      sequenceId: sequence.id,
      blockedReason: `PES encoding failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }

  // --- verify the produced bytes ---------------------------------------
  const verification = verifyPesBytes(bytes, design, sequence);
  const allPassed = verification.every((c) => c.passed);

  if (!allPassed) {
    const failed = verification.filter((c) => !c.passed).map((c) => c.detail);
    return {
      ok: false,
      fileName,
      validation,
      verification,
      sequenceId: sequence.id,
      blockedReason: `The generated file did not pass verification, so it was not offered for download: ${failed.join(' ')}`,
    };
  }

  return { ok: true, bytes, fileName, validation, verification, sequenceId: sequence.id };
}

/**
 * Decode the freshly written bytes and compare against the design they came
 * from. This catches encoder regressions before a file ever reaches a machine.
 */
export function verifyPesBytes(
  bytes: Uint8Array,
  design: EmbroideryDesign,
  sequence: StitchSequence,
): ExportCheck[] {
  const checks: ExportCheck[] = [];
  const stats = computeStats(design);
  const expectedColorBlocks = sequence.blocks.length;

  checks.push({
    name: 'Signature',
    passed: bytes.length > 8 && String.fromCharCode(...bytes.subarray(0, 8)) === '#PES0001',
    detail: `File starts with "${String.fromCharCode(...bytes.subarray(0, 8)).replace(/[^\x20-\x7e]/g, '?')}".`,
  });

  let reread: ReturnType<typeof readPes>;
  try {
    reread = readPes(bytes);
    checks.push({ name: 'Re-read', passed: true, detail: 'The written file parses back as a PES file.' });
  } catch (err) {
    checks.push({
      name: 'Re-read',
      passed: false,
      detail: `The written file could not be parsed back: ${err instanceof Error ? err.message : String(err)}`,
    });
    return checks;
  }

  const rereadStitches = reread.stitches.filter((s) => s.command === StitchCommand.Stitch).length;
  checks.push({
    name: 'Stitch count',
    passed: rereadStitches === stats.stitchCount,
    detail: `Design has ${stats.stitchCount} stitches; the file decodes to ${rereadStitches}.`,
  });

  checks.push({
    name: 'Colour blocks',
    passed: reread.threads.length === expectedColorBlocks,
    detail: `Design needs ${expectedColorBlocks} colour block(s); the file declares ${reread.threads.length}.`,
  });

  // The colour a machine will actually load at each stop, in order.
  const expectedColors = sequence.blockThreads.map((t) => `${t.r},${t.g},${t.b}`);
  const actualColors = reread.threads.map((t) => `${t.r},${t.g},${t.b}`);
  checks.push({
    name: 'Colour order',
    passed: expectedColors.join('|') === actualColors.join('|'),
    detail:
      expectedColors.join('|') === actualColors.join('|')
        ? `All ${expectedColors.length} colour stop(s) decode to the thread the design assigned.`
        : `Colour stops differ. Design: ${expectedColors.join(' ')}. File: ${actualColors.join(' ')}.`,
  });

  // Dimensions, measured from the decoded stitches.
  const xs = reread.stitches.filter((s) => s.command === StitchCommand.Stitch);
  if (xs.length > 0) {
    const minX = Math.min(...xs.map((s) => s.x));
    const maxX = Math.max(...xs.map((s) => s.x));
    const minY = Math.min(...xs.map((s) => s.y));
    const maxY = Math.max(...xs.map((s) => s.y));
    const w = maxX - minX;
    const h = maxY - minY;
    // Coordinates are stored as integers, so allow one unit of rounding.
    const widthOk = Math.abs(w - stats.width) <= 1.5;
    const heightOk = Math.abs(h - stats.height) <= 1.5;
    checks.push({
      name: 'Dimensions',
      passed: widthOk && heightOk,
      detail: `Design is ${(stats.width / 10).toFixed(1)} x ${(stats.height / 10).toFixed(1)} mm; the file decodes to ${(w / 10).toFixed(1)} x ${(h / 10).toFixed(1)} mm.`,
    });
  }

  return checks;
}

export function sanitizeFileName(name: string): string {
  const cleaned = name.replace(/[^a-zA-Z0-9-_ ]/g, '').trim().replace(/\s+/g, '-');
  return cleaned.length ? cleaned.slice(0, 60) : 'design';
}
