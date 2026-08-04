/**
 * Stitch-out worksheet.
 *
 * A printable sheet that goes to the machine with the hooped sample. It records
 * what the software predicted, so the physical result can be compared against
 * it line by line instead of from memory. The blanks are for the operator to
 * fill in at the machine — the application never fills them itself.
 */

import type { EmbroideryDesign } from '../domain/design';
import { formatRuntime } from '../domain/design';
import type { Hoop, MachineProfile } from '../domain/machine';
import type { ColorStop, StitchSequence } from '../domain/stitch-sequence';
import { unitsToInches, unitsToMm } from '../domain/units';

export interface WorksheetInput {
  design: EmbroideryDesign;
  machine: MachineProfile;
  hoop: Hoop | null;
  sequence: StitchSequence;
  stops: ColorStop[];
  /** File the operator will load, when one has been exported. */
  exportedFileName: string | null;
}

export function stitchOutWorksheetText(input: WorksheetInput): string {
  const { design, machine, hoop, sequence, stops } = input;
  const s = sequence.stats;
  const line = (label: string, value: string): string => `${label.padEnd(24)}${value}`;

  const out: string[] = [];
  out.push('NORTON THREAD CO. — PHYSICAL STITCH-OUT WORKSHEET');
  out.push('='.repeat(66));
  out.push('');
  out.push(line('Design', design.metadata.name));
  if (design.metadata.customer) out.push(line('Customer', design.metadata.customer));
  out.push(line('Machine', machine.name));
  out.push(line('Hoop', hoop?.name ?? '(none selected)'));
  out.push(line('File to load', input.exportedFileName ?? '(not exported yet)'));
  out.push(line('Stitch sequence', sequence.id));
  out.push(line('Prepared', new Date().toISOString()));
  out.push('');

  out.push('WHAT THE SOFTWARE PREDICTS');
  out.push('-'.repeat(66));
  out.push(line('Stitch count', s.stitchCount.toLocaleString()));
  out.push(
    line(
      'Design size',
      `${unitsToMm(s.width).toFixed(1)} x ${unitsToMm(s.height).toFixed(1)} mm  (${unitsToInches(s.width).toFixed(2)} x ${unitsToInches(s.height).toFixed(2)} in)`,
    ),
  );
  out.push(line('Colour stops', String(sequence.blocks.length)));
  out.push(line('Thread cones', String(sequence.distinctThreads.length)));
  out.push(line('Colour changes', String(s.colorChangeCount)));
  out.push(line('Trims', String(s.trimCount)));
  out.push(line('Jumps', String(s.jumpCount)));
  out.push(line('Est. run time', formatRuntime(s.estimatedRuntimeSeconds)));
  out.push(
    line('Stitch length range', `${unitsToMm(s.minStitchLength).toFixed(2)} – ${unitsToMm(s.maxStitchLength).toFixed(2)} mm`),
  );
  out.push('');

  out.push('COLOUR STOPS (follow in this order)');
  out.push('-'.repeat(66));
  out.push('  #   Thread                      Code     Stitches   Note');
  for (const stop of stops) {
    const note = stop.repeatOfStep !== null ? `same cone as #${stop.repeatOfStep}` : '';
    out.push(
      `  ${String(stop.step).padEnd(4)}${stop.thread.name.padEnd(28)}${stop.thread.code.padEnd(9)}${String(
        stop.stitchCount,
      ).padEnd(11)}${note}`,
    );
  }
  out.push('');

  out.push('MACHINE READOUT — fill in from the machine before starting');
  out.push('-'.repeat(66));
  out.push('  Machine lists the design from USB ......  [ ] yes   [ ] no');
  out.push('  Stitch count shown on machine ..........  ______________');
  out.push('  Colour stops shown on machine ..........  ______________');
  out.push('  Design size shown on machine ...........  ______________');
  out.push('  Matches the predictions above ..........  [ ] yes   [ ] no');
  out.push('');

  out.push('SET-UP');
  out.push('-'.repeat(66));
  out.push('  Fabric ..................................  ______________________');
  out.push('  Stabiliser ..............................  ______________________');
  out.push('  Needle ..................................  ______________________');
  out.push('  Top thread weight/brand .................  ______________________');
  out.push('  Bobbin ..................................  ______________________');
  out.push('  Machine speed ...........................  ______________________');
  out.push('');

  out.push('RESULT — fill in after sewing');
  out.push('-'.repeat(66));
  out.push('  Completed without stopping ..............  [ ] yes   [ ] no');
  out.push('  Thread breaks (count) ...................  ______________');
  out.push('  Bird-nesting / bobbin issues ............  [ ] none  [ ] some  [ ] severe');
  out.push('  Registration between colours ............  [ ] good  [ ] fair  [ ] poor');
  out.push('  Satin columns held ......................  [ ] yes   [ ] no    [ ] n/a');
  out.push('  Fabric perforated / too dense ...........  [ ] no    [ ] yes');
  out.push('  Small detail legible ....................  [ ] yes   [ ] no    [ ] n/a');
  out.push('  Measured finished width .................  ______________ mm');
  out.push('  Measured finished height ................  ______________ mm');
  out.push('  Actual run time .........................  ______________');
  out.push('');
  out.push('  Overall .................................  [ ] acceptable  [ ] needs work  [ ] failed');
  out.push('');
  out.push('  Notes:');
  for (let i = 0; i < 5; i++) out.push('  ' + '_'.repeat(62));
  out.push('');
  out.push('  Operator ______________________   Date ______________');
  out.push('');
  out.push('-'.repeat(66));
  out.push('Keep this sheet with the sewn sample and the artwork print-out.');
  out.push('Until this sheet is completed and accepted, this design is NOT');
  out.push(`verified on a ${machine.name}.`);

  return out.join('\n');
}
