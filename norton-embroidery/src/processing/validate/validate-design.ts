/**
 * Design validation.
 *
 * Every message states the measured value and the limit it broke, because
 * "something went wrong" is useless to an operator standing at a machine.
 *
 * ERROR   - export is blocked.
 * WARNING - export allowed once the operator acknowledges it.
 * INFO    - informational only.
 */

import { boundsHeight, boundsWidth, distance } from '../../domain/geometry';
import { computeStats, type EmbroideryDesign } from '../../domain/design';
import { getHoop, type Hoop, type MachineProfile } from '../../domain/machine';
import { StitchCommand } from '../../domain/stitch';
import { buildStitchSequence } from '../../domain/stitch-sequence';
import { buildReport, sortIssues, type ValidationIssue, type ValidationReport } from '../../domain/validation';
import { unitsToInches, unitsToMm } from '../../domain/units';
import { objectPoints, DEFAULTS } from '../../domain/embroidery-object';
import { columnWidths, columnFromRing } from '../stitches/satin';

export interface ValidationThresholds {
  /** Warn above this many stitches. */
  stitchCountWarning: number;
  /** Warn above this many colour changes. */
  colorChangeWarning: number;
  /** Warn above this many trims. */
  trimWarning: number;
  /** Warn when a fill row spacing is tighter than this, 0.1 mm units. */
  minDensity: number;
  /** Warn when a satin column is narrower than this, 0.1 mm units. */
  minSatinWidth: number;
  /** Warn when a satin column is wider than this, 0.1 mm units. */
  maxSatinWidth: number;
  /** Objects with fewer stitches than this are probably not worth sewing. */
  minObjectStitches: number;
}

export const DEFAULT_THRESHOLDS: ValidationThresholds = {
  stitchCountWarning: 30000,
  colorChangeWarning: 8,
  trimWarning: 40,
  minDensity: 3, // 0.3 mm
  minSatinWidth: DEFAULTS.satinMinWidth,
  maxSatinWidth: DEFAULTS.satinMaxWidth,
  minObjectStitches: 4,
};

let issueCounter = 0;
const nextIssueId = (): string => `issue-${++issueCounter}`;

function issue(
  severity: ValidationIssue['severity'],
  code: string,
  message: string,
  extra: Partial<ValidationIssue> = {},
): ValidationIssue {
  return { id: nextIssueId(), severity, code, message, ...extra };
}

export function validateDesign(
  design: EmbroideryDesign,
  machine: MachineProfile,
  thresholds: ValidationThresholds = DEFAULT_THRESHOLDS,
): ValidationReport {
  const issues: ValidationIssue[] = [];
  const hoop = getHoop(machine, design.canvas.hoopId);

  if (!hoop) {
    issues.push(
      issue('ERROR', 'hoop.missing', `Hoop "${design.canvas.hoopId}" is not available on the ${machine.name}. Select a hoop before exporting.`),
    );
  }

  // --- empty design -----------------------------------------------------
  const stats = computeStats(design);
  if (stats.stitchCount === 0) {
    issues.push(
      issue('ERROR', 'design.empty', 'The design contains no stitches. Digitize the artwork or add an object before exporting.', {
        remedy: 'Run "Digitize" on the uploaded artwork.',
      }),
    );
    return buildReport(sortIssues(issues));
  }

  if (design.objects.length === 0) {
    issues.push(
      issue('WARNING', 'design.no-objects', 'The design has stitches but no editable objects. It was probably imported from a stitch file, so shapes cannot be re-generated.'),
    );
  }

  // --- coordinate sanity ------------------------------------------------
  let invalidCoords = 0;
  let firstInvalid = -1;
  for (let i = 0; i < design.stitches.length; i++) {
    const s = design.stitches[i];
    if (!Number.isFinite(s.x) || !Number.isFinite(s.y)) {
      invalidCoords++;
      if (firstInvalid < 0) firstInvalid = i;
    }
  }
  if (invalidCoords > 0) {
    issues.push(
      issue('ERROR', 'stitch.invalid-coordinate', `${invalidCoords} stitch coordinate(s) are not finite numbers (first at stitch ${firstInvalid}). The file cannot be encoded.`, {
        stitchIndex: firstInvalid,
        remedy: 'Re-run digitizing, or delete the object that produced the bad geometry.',
      }),
    );
  }

  const last = design.stitches[design.stitches.length - 1];
  if (!last || last.command !== StitchCommand.End) {
    issues.push(
      issue('ERROR', 'stitch.no-end', 'The stitch stream does not finish with an End command, so a machine would not know where the design stops.'),
    );
  }

  // --- hoop fit ---------------------------------------------------------
  if (hoop) {
    issues.push(...validateHoopFit(stats.width, stats.height, hoop, design, machine));
  }

  // --- stitch length ----------------------------------------------------
  let overlong = 0;
  let longestSeen = 0;
  for (let i = 1; i < design.stitches.length; i++) {
    const a = design.stitches[i - 1];
    const b = design.stitches[i];
    if (a.command !== StitchCommand.Stitch || b.command !== StitchCommand.Stitch) continue;
    const d = distance(a, b);
    if (d > longestSeen) longestSeen = d;
    if (d > machine.maxStitchLength) overlong++;
  }
  if (overlong > 0) {
    issues.push(
      issue('ERROR', 'stitch.too-long', `${overlong} stitch(es) are longer than the ${unitsToMm(machine.maxStitchLength).toFixed(1)} mm maximum for the ${machine.name} (longest is ${unitsToMm(longestSeen).toFixed(1)} mm).`, {
        remedy: 'Reduce the stitch length on the affected objects, or split wide satin columns into fills.',
      }),
    );
  }

  if (stats.minStitchLength > 0 && stats.minStitchLength < DEFAULTS.minStitchLength) {
    issues.push(
      issue('WARNING', 'stitch.too-short', `The shortest stitch is ${unitsToMm(stats.minStitchLength).toFixed(2)} mm. Stitches under ${unitsToMm(DEFAULTS.minStitchLength).toFixed(1)} mm cause thread breaks and can damage the needle.`, {
        remedy: 'Increase density spacing or simplify the geometry in that area.',
      }),
    );
  }

  // --- stitch count -----------------------------------------------------
  if (machine.maxStitchCount !== null && stats.stitchCount > machine.maxStitchCount) {
    issues.push(
      issue('ERROR', 'design.stitch-count', `The design has ${stats.stitchCount.toLocaleString()} stitches; the ${machine.name} accepts at most ${machine.maxStitchCount.toLocaleString()}.`),
    );
  } else if (stats.stitchCount > thresholds.stitchCountWarning) {
    issues.push(
      issue('WARNING', 'design.stitch-count-high', `${stats.stitchCount.toLocaleString()} stitches is a long run (roughly ${Math.round(stats.estimatedRuntimeSeconds / 60)} minutes). Dense designs also stiffen the garment.`, {
        remedy: 'Reduce fill density or simplify small detail to bring the count down.',
      }),
    );
  }

  // --- colours ----------------------------------------------------------
  if (design.threadPalette.length === 0) {
    issues.push(issue('ERROR', 'thread.empty-palette', 'The design has no thread palette, so colours cannot be written to the file.'));
  }

  // A design may leave a colour and come back to it, so there can legitimately
  // be more colour blocks than palette entries. What must hold is that every
  // block resolves to a real thread.
  const sequence = buildStitchSequence(design);
  if (sequence.blockThreads.length !== sequence.blocks.length) {
    const unresolved = sequence.blocks.length - sequence.blockThreads.length;
    issues.push(
      issue('ERROR', 'thread.sequence-mismatch', `${unresolved} of ${sequence.blocks.length} colour block(s) do not resolve to a thread in the palette. The colour sequence is inconsistent.`, {
        remedy: 'Re-generate stitches so the palette and the colour blocks match.',
      }),
    );
  }

  if (design.colorSequence && design.colorSequence.length !== sequence.blocks.length) {
    issues.push(
      issue('ERROR', 'thread.sequence-length', `The design records ${design.colorSequence.length} colour assignment(s) but the stitch stream has ${sequence.blocks.length} colour block(s). Re-generate the stitches.`),
    );
  }

  if (sequence.hasRepeatedColors) {
    issues.push(
      issue('INFO', 'thread.repeated-colors', `${sequence.blocks.length} colour stop(s) use ${sequence.distinctThreads.length} thread cone(s): at least one colour is used, left, and returned to. Follow the colour list in sew order, not the thread list.`),
    );
  }

  if (machine.maxColorChanges !== null && stats.colorChangeCount > machine.maxColorChanges) {
    issues.push(
      issue('ERROR', 'design.color-changes', `${stats.colorChangeCount} colour changes exceed the ${machine.maxColorChanges} supported by the ${machine.name}.`),
    );
  } else if (stats.colorChangeCount > thresholds.colorChangeWarning) {
    issues.push(
      issue('WARNING', 'design.color-changes-high', `${stats.colorChangeCount} colour changes. On a ${machine.needles}-needle machine the operator re-threads at every one of them.`, {
        remedy: 'Merge similar colours to reduce thread changes.',
      }),
    );
  }

  // --- trims and jumps --------------------------------------------------
  if (stats.trimCount > thresholds.trimWarning) {
    issues.push(
      issue('WARNING', 'design.trims-high', `${stats.trimCount} thread trims. Each one is a stop-and-cut, and loose ends have to be clipped by hand afterwards.`, {
        remedy: 'Increase the travel threshold so short hops are sewn instead of cut.',
      }),
    );
  }

  // --- per-object checks ------------------------------------------------
  for (const obj of design.objects) {
    const pts = objectPoints(obj);
    if (pts.length === 0) {
      issues.push(
        issue('ERROR', 'object.empty-geometry', `Object "${obj.name}" has no geometry.`, { objectIds: [obj.id] }),
      );
      continue;
    }
    if (pts.some((p) => !Number.isFinite(p.x) || !Number.isFinite(p.y))) {
      issues.push(
        issue('ERROR', 'object.corrupt-geometry', `Object "${obj.name}" contains non-finite coordinates and cannot be stitched.`, { objectIds: [obj.id] }),
      );
      continue;
    }

    const b = { minX: Math.min(...pts.map((p) => p.x)), maxX: Math.max(...pts.map((p) => p.x)), minY: Math.min(...pts.map((p) => p.y)), maxY: Math.max(...pts.map((p) => p.y)) };
    const w = boundsWidth(b);
    const h = boundsHeight(b);

    if (obj.type === 'fill' && obj.density < thresholds.minDensity) {
      issues.push(
        issue('WARNING', 'object.density-high', `Object "${obj.name}" fills at ${unitsToMm(obj.density).toFixed(2)} mm row spacing. Below ${unitsToMm(thresholds.minDensity).toFixed(1)} mm the fabric perforates and the thread piles up.`, {
          objectIds: [obj.id],
          remedy: 'Increase the row spacing to 0.4 mm or more.',
        }),
      );
    }

    if (obj.type === 'satin') {
      const column =
        obj.geometry.kind === 'column'
          ? { left: obj.geometry.left, right: obj.geometry.right }
          : obj.geometry.kind === 'polygon'
            ? columnFromRing(obj.geometry.polygon.outer)
            : null;
      if (column) {
        const widths = columnWidths(column);
        const minW = Math.min(...widths);
        const maxW = Math.max(...widths);
        if (minW < thresholds.minSatinWidth) {
          issues.push(
            issue('WARNING', 'object.satin-narrow', `Satin column "${obj.name}" narrows to ${unitsToMm(minW).toFixed(2)} mm. Columns under ${unitsToMm(thresholds.minSatinWidth).toFixed(1)} mm tend to pull into a hole rather than a stitch.`, {
              objectIds: [obj.id],
              remedy: 'Widen the shape, enlarge the design, or convert this object to a running stitch.',
            }),
          );
        }
        if (maxW > thresholds.maxSatinWidth) {
          issues.push(
            issue('WARNING', 'object.satin-wide', `Satin column "${obj.name}" reaches ${unitsToMm(maxW).toFixed(1)} mm wide. Long satin stitches snag; ${unitsToMm(thresholds.maxSatinWidth).toFixed(0)} mm is the practical limit.`, {
              objectIds: [obj.id],
              remedy: 'Convert this object to a fill.',
            }),
          );
        }
      }
    }

    if (Math.max(w, h) < DEFAULTS.minStitchLength * 2) {
      issues.push(
        issue('WARNING', 'object.tiny', `Object "${obj.name}" is only ${unitsToMm(Math.max(w, h)).toFixed(2)} mm across. Detail this small will not read once sewn.`, {
          objectIds: [obj.id],
          remedy: 'Delete it, or scale the design up.',
        }),
      );
    }

    for (const note of obj.notes) {
      issues.push(issue('INFO', 'object.note', `"${obj.name}": ${note}`, { objectIds: [obj.id] }));
    }
  }

  // --- informational ----------------------------------------------------
  issues.push(
    issue('INFO', 'design.summary', `${stats.stitchCount.toLocaleString()} stitches, ${stats.colorCount} colour block(s), ${stats.trimCount} trim(s), ${unitsToMm(stats.width).toFixed(1)} x ${unitsToMm(stats.height).toFixed(1)} mm.`),
  );

  return buildReport(sortIssues(issues));
}

function validateHoopFit(
  width: number,
  height: number,
  hoop: Hoop,
  design: EmbroideryDesign,
  machine: MachineProfile,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];

  if (width > hoop.width) {
    const over = width - hoop.width;
    issues.push(
      issue('ERROR', 'hoop.width', `Design width exceeds the ${hoop.name} hoop by ${unitsToInches(over).toFixed(2)} inches (${unitsToMm(over).toFixed(1)} mm). Design is ${unitsToMm(width).toFixed(1)} mm wide; the hoop field is ${unitsToMm(hoop.width).toFixed(1)} mm.`, {
        remedy: 'Reduce the design size, rotate it, or select a larger hoop.',
      }),
    );
  }
  if (height > hoop.height) {
    const over = height - hoop.height;
    issues.push(
      issue('ERROR', 'hoop.height', `Design height exceeds the ${hoop.name} hoop by ${unitsToInches(over).toFixed(2)} inches (${unitsToMm(over).toFixed(1)} mm). Design is ${unitsToMm(height).toFixed(1)} mm tall; the hoop field is ${unitsToMm(hoop.height).toFixed(1)} mm.`, {
        remedy: 'Reduce the design size, rotate it, or select a larger hoop.',
      }),
    );
  }

  const safeWidth = hoop.width - hoop.safetyMargin * 2;
  const safeHeight = hoop.height - hoop.safetyMargin * 2;
  if (width <= hoop.width && width > safeWidth) {
    issues.push(
      issue('WARNING', 'hoop.safe-area-width', `Design width (${unitsToMm(width).toFixed(1)} mm) is inside the hoop but within ${unitsToMm(hoop.safetyMargin).toFixed(1)} mm of its edge. The presser foot can catch the hoop frame.`, {
        remedy: `Keep the design under ${unitsToMm(safeWidth).toFixed(1)} mm wide.`,
      }),
    );
  }
  if (height <= hoop.height && height > safeHeight) {
    issues.push(
      issue('WARNING', 'hoop.safe-area-height', `Design height (${unitsToMm(height).toFixed(1)} mm) is inside the hoop but within ${unitsToMm(hoop.safetyMargin).toFixed(1)} mm of its edge.`, {
        remedy: `Keep the design under ${unitsToMm(safeHeight).toFixed(1)} mm tall.`,
      }),
    );
  }

  const canvasFits = design.canvas.width <= hoop.width && design.canvas.height <= hoop.height;
  if (!canvasFits) {
    issues.push(
      issue('WARNING', 'hoop.canvas', `The requested design size (${unitsToMm(design.canvas.width).toFixed(1)} x ${unitsToMm(design.canvas.height).toFixed(1)} mm) does not fit the ${hoop.name} hoop on the ${machine.name}.`),
    );
  }

  return issues;
}
