/**
 * Automatic fixes for validation problems.
 *
 * Only problems with one obviously-correct, deterministic remedy appear here.
 * A fix qualifies when a competent operator would always do the same thing —
 * scaling a design that overhangs the hoop, centring one that sits in the
 * margin, opening up a fill that is packed tighter than the fabric can take.
 *
 * Anything requiring judgement is deliberately absent: what to do about a
 * detail too small to read, or artwork the analyser rated poorly, depends on
 * what the design is for. Those stay as warnings the person has to answer.
 */

import { computeStats, type EmbroideryDesign } from '../domain/design';
import { DEFAULTS } from '../domain/embroidery-object';
import { getHoop, type MachineProfile } from '../domain/machine';
import { unitsToMm } from '../domain/units';
import { centerDesign, scaleDesign, setObjectProperty } from './editor-ops';
import { DEFAULT_THRESHOLDS } from '../processing/validate/validate-design';

export interface AutoFix {
  /** Validation issue code this addresses. */
  code: string;
  /** Button label. Says what will happen, in the user's terms. */
  label: string;
  /** One sentence naming the concrete change, with the numbers. */
  detail: string;
  apply: (design: EmbroideryDesign, machine: MachineProfile) => EmbroideryDesign;
}

/**
 * Fixes available for the design's current validation report, in the order
 * they should be applied. Returns an empty list when nothing is safely
 * automatable — which is the common case for a clean design.
 */
export function suggestAutoFixes(design: EmbroideryDesign, machine: MachineProfile): AutoFix[] {
  const report = design.validation;
  if (!report || design.stitches.length === 0) return [];

  const fixes: AutoFix[] = [];
  const codes = new Set(report.issues.map((i) => i.code));
  const hoop = getHoop(machine, design.canvas.hoopId);
  const stats = computeStats(design);

  // --- too big for the hoop --------------------------------------------
  if (hoop && (codes.has('hoop.width') || codes.has('hoop.height'))) {
    const safeW = hoop.width - hoop.safetyMargin * 2;
    const safeH = hoop.height - hoop.safetyMargin * 2;
    const factor = Math.min(safeW / stats.width, safeH / stats.height);
    if (Number.isFinite(factor) && factor > 0 && factor < 1) {
      const targetW = unitsToMm(stats.width * factor);
      const targetH = unitsToMm(stats.height * factor);
      fixes.push({
        code: 'hoop.width',
        label: 'Resize to fit the hoop',
        detail: `Scale the whole design to ${targetW.toFixed(1)} x ${targetH.toFixed(1)} mm and centre it in the ${hoop.name} hoop.`,
        apply: (d, m) => {
          const scaled = scaleDesign(d, m, factor);
          return centerDesign(scaled, m, hoop.width, hoop.height);
        },
      });
    }
  }

  // --- inside the hoop but in the margin --------------------------------
  if (
    hoop &&
    !codes.has('hoop.width') &&
    !codes.has('hoop.height') &&
    (codes.has('hoop.safe-area-width') || codes.has('hoop.safe-area-height'))
  ) {
    fixes.push({
      code: 'hoop.safe-area-width',
      label: 'Centre in the hoop',
      detail: `Move the design to the middle of the ${hoop.name} hoop so it clears the frame.`,
      apply: (d, m) => centerDesign(d, m, hoop.width, hoop.height),
    });
  }

  // --- fill packed too tightly ------------------------------------------
  const dense = design.objects.filter(
    (o) => o.type === 'fill' && o.density < DEFAULT_THRESHOLDS.minDensity,
  );
  if (dense.length > 0) {
    const target = DEFAULT_THRESHOLDS.minDensity;
    fixes.push({
      code: 'object.density-high',
      label: 'Loosen the dense areas',
      detail: `Open ${dense.length} filled area${dense.length === 1 ? '' : 's'} out to ${unitsToMm(target).toFixed(2)} mm row spacing so the fabric is not perforated.`,
      apply: (d, m) => {
        let next = d;
        for (const obj of dense) {
          next = setObjectProperty(obj.id, 'density', target)(next, m);
        }
        return next;
      },
    });
  }

  // --- stitches longer than the machine allows --------------------------
  const overlong = design.objects.filter((o) => o.stitchLength > machine.maxStitchLength);
  if (codes.has('stitch.too-long') && overlong.length > 0) {
    const target = Math.min(machine.maxStitchLength, DEFAULTS.maxStitchLength);
    fixes.push({
      code: 'stitch.too-long',
      label: 'Shorten the long stitches',
      detail: `Cap the stitch length on ${overlong.length} object${overlong.length === 1 ? '' : 's'} at ${unitsToMm(target).toFixed(1)} mm, the maximum for the ${machine.name}.`,
      apply: (d, m) => {
        let next = d;
        for (const obj of overlong) {
          next = setObjectProperty(obj.id, 'stitchLength', target)(next, m);
        }
        return next;
      },
    });
  }

  return fixes;
}

/** Apply every available fix, re-deriving the list after each one. */
export function applyAllAutoFixes(
  design: EmbroideryDesign,
  machine: MachineProfile,
): { design: EmbroideryDesign; applied: string[] } {
  let current = design;
  const applied: string[] = [];
  // Each fix regenerates and revalidates, so the next round sees real state.
  // Bounded so a fix that fails to clear its own issue cannot loop.
  for (let round = 0; round < 6; round++) {
    const fixes = suggestAutoFixes(current, machine);
    if (fixes.length === 0) break;
    const next = fixes[0].apply(current, machine);
    applied.push(fixes[0].label);
    current = next;
  }
  return { design: current, applied };
}
