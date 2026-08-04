/**
 * Production readiness.
 *
 * Three different things get called "validated", and conflating them is how an
 * operator ends up trusting a file that has never touched a machine:
 *
 *   1. SOFTWARE      - the design passes this application's own rules.
 *   2. PES FORMAT    - the bytes decode back to the design they came from, and
 *                      an independent implementation reads them the same way.
 *   3. MACHINE       - a physical stitch-out ran on the target machine and the
 *                      result was acceptable.
 *
 * This application can establish 1 and 2. It cannot establish 3, and it never
 * claims to: machine readiness stays `not-performed` until a human records a
 * stitch-out. Nothing in the code can move it.
 */

import type { EmbroideryDesign } from './design';
import type { MachineProfile } from './machine';
import { buildStitchSequence, type StitchSequence } from './stitch-sequence';

export type TierState = 'not-started' | 'in-progress' | 'passed' | 'failed' | 'not-performed';

export interface ReadinessTier {
  id: 'software' | 'pes-format' | 'machine';
  title: string;
  state: TierState;
  /** One-line summary an operator can act on. */
  summary: string;
  /** Individual checks behind the summary. */
  checks: Array<{ label: string; state: TierState; detail: string }>;
}

export interface ReadinessReport {
  tiers: ReadinessTier[];
  /** True only when software and PES-format tiers both pass. */
  readyForPhysicalTest: boolean;
  /** Always false. Only a recorded stitch-out can change this. */
  provenOnMachine: false;
  sequenceId: string | null;
  generatedAt: string;
}

export interface ExportSnapshot {
  ok: boolean;
  sequenceId: string;
  fileName: string;
  byteLength: number;
  checks: Array<{ name: string; passed: boolean; detail: string }>;
  blockedReason?: string;
}

export interface ReadinessInput {
  design: EmbroideryDesign;
  machine: MachineProfile;
  /** Whether artwork has been analysed in this session. */
  artworkLoaded: boolean;
  /** The last export attempt, if any. */
  lastExport: ExportSnapshot | null;
}

export function buildReadinessReport(input: ReadinessInput): ReadinessReport {
  const { design, machine, artworkLoaded, lastExport } = input;
  const sequence: StitchSequence | null =
    design.stitches.length > 0 ? buildStitchSequence(design) : null;

  return {
    tiers: [
      softwareTier(design, machine, artworkLoaded, sequence),
      pesTier(lastExport, sequence),
      machineTier(machine),
    ],
    readyForPhysicalTest:
      design.validation?.passed === true && lastExport?.ok === true && lastExport.sequenceId === sequence?.id,
    provenOnMachine: false,
    sequenceId: sequence?.id ?? null,
    generatedAt: new Date().toISOString(),
  };
}

function softwareTier(
  design: EmbroideryDesign,
  machine: MachineProfile,
  artworkLoaded: boolean,
  sequence: StitchSequence | null,
): ReadinessTier {
  const checks: ReadinessTier['checks'] = [];

  checks.push({
    label: 'Artwork loaded',
    state: artworkLoaded ? 'passed' : 'not-started',
    detail: artworkLoaded ? 'An artwork file has been read and analysed.' : 'No artwork has been uploaded.',
  });

  checks.push({
    label: 'Digitization',
    state: design.objects.length > 0 ? 'passed' : design.stitches.length > 0 ? 'not-performed' : 'not-started',
    detail:
      design.objects.length > 0
        ? `${design.objects.length} embroidery object(s) generated.`
        : design.stitches.length > 0
          ? 'Stitches came from an imported file, so there are no editable objects.'
          : 'The artwork has not been digitized yet.',
  });

  checks.push({
    label: 'Stitch generation',
    state: sequence ? 'passed' : 'not-started',
    detail: sequence
      ? `${sequence.stats.stitchCount.toLocaleString()} stitches across ${sequence.blocks.length} colour block(s).`
      : 'No stitches have been generated.',
  });

  const report = design.validation;
  checks.push({
    label: `Rules for ${machine.name}`,
    state: !report ? 'not-started' : report.passed ? 'passed' : 'failed',
    detail: !report
      ? 'The design has not been validated.'
      : report.passed
        ? `No blocking errors. ${report.warningCount} warning(s), ${report.infoCount} note(s).`
        : `${report.errorCount} error(s) block export.`,
  });

  const state: TierState = checks.some((c) => c.state === 'failed')
    ? 'failed'
    : checks.every((c) => c.state === 'passed')
      ? 'passed'
      : checks.some((c) => c.state === 'passed')
        ? 'in-progress'
        : 'not-started';

  return {
    id: 'software',
    title: 'Software validated',
    state,
    summary:
      state === 'passed'
        ? `The design satisfies this application's rules for the ${machine.name}.`
        : state === 'failed'
          ? 'The design breaks at least one rule and cannot be exported.'
          : 'The design has not been taken through the full pipeline yet.',
    checks,
  };
}

function pesTier(lastExport: ExportSnapshot | null, sequence: StitchSequence | null): ReadinessTier {
  if (!lastExport) {
    return {
      id: 'pes-format',
      title: 'PES format validated',
      state: 'not-started',
      summary: 'No PES file has been exported from this design yet.',
      checks: [],
    };
  }

  const checks: ReadinessTier['checks'] = lastExport.checks.map((c) => ({
    label: c.name,
    state: c.passed ? ('passed' as TierState) : ('failed' as TierState),
    detail: c.detail,
  }));

  const matchesPreview = sequence !== null && lastExport.sequenceId === sequence.id;
  checks.push({
    label: 'Matches the preview',
    state: matchesPreview ? 'passed' : 'failed',
    detail: matchesPreview
      ? `The exported file and the on-screen preview were both built from stitch sequence ${lastExport.sequenceId}.`
      : `The design has changed since this file was exported (file ${lastExport.sequenceId}, preview ${sequence?.id ?? 'none'}). Export again before sewing.`,
  });

  const state: TierState = !lastExport.ok
    ? 'failed'
    : checks.every((c) => c.state === 'passed')
      ? 'passed'
      : 'failed';

  return {
    id: 'pes-format',
    title: 'PES format validated',
    state,
    summary:
      state === 'passed'
        ? `${lastExport.fileName} (${(lastExport.byteLength / 1024).toFixed(1)} kB) was written and decoded back to the same design.`
        : (lastExport.blockedReason ?? 'The exported file did not pass verification.'),
    checks,
  };
}

function machineTier(machine: MachineProfile): ReadinessTier {
  return {
    id: 'machine',
    title: 'Machine validated',
    // This is deliberately hard-coded. No code path may set it to 'passed'.
    state: 'not-performed',
    summary:
      `NOT VERIFIED ON HARDWARE. No design from this application has been confirmed to sew correctly on a ${machine.name}. ` +
      'Software and file-format checks do not prove a physical stitch-out.',
    checks: [
      {
        label: 'Physical stitch-out',
        state: 'not-performed',
        detail:
          'Run the procedure in docs/PHYSICAL-VALIDATION.md on scrap fabric, on the actual machine, before any customer job.',
      },
      {
        label: 'Machine accepted the file',
        state: 'not-performed',
        detail: `Confirm the ${machine.name} lists the design from USB and reports the expected stitch count and colour stops.`,
      },
      {
        label: 'Registration and density',
        state: 'not-performed',
        detail: 'Confirm colour areas line up, satin columns hold, and the fabric is not perforated.',
      },
    ],
  };
}

export function tierLabel(state: TierState): string {
  switch (state) {
    case 'passed':
      return 'PASSED';
    case 'failed':
      return 'FAILED';
    case 'in-progress':
      return 'IN PROGRESS';
    case 'not-performed':
      return 'NOT PERFORMED';
    case 'not-started':
      return 'NOT STARTED';
  }
}

/** Plain-text readiness report, saved alongside a PES for the machine test. */
export function readinessReportText(
  report: ReadinessReport,
  design: EmbroideryDesign,
  machine: MachineProfile,
): string {
  const lines: string[] = [];
  lines.push('NORTON THREAD CO. — PRODUCTION READINESS REPORT');
  lines.push('='.repeat(60));
  lines.push(`Design:    ${design.metadata.name}`);
  if (design.metadata.customer) lines.push(`Customer:  ${design.metadata.customer}`);
  lines.push(`Machine:   ${machine.name}`);
  lines.push(`Sequence:  ${report.sequenceId ?? 'none'}`);
  lines.push(`Generated: ${report.generatedAt}`);
  lines.push('');

  for (const tier of report.tiers) {
    lines.push(`[${tierLabel(tier.state).padEnd(12)}] ${tier.title}`);
    lines.push(`  ${tier.summary}`);
    for (const check of tier.checks) {
      lines.push(`    - ${check.label}: ${tierLabel(check.state)}`);
      lines.push(`      ${check.detail}`);
    }
    lines.push('');
  }

  lines.push('-'.repeat(60));
  lines.push('WHAT THIS REPORT DOES AND DOES NOT ESTABLISH');
  lines.push('');
  lines.push('  Established: the design passes this application\'s rules, and the');
  lines.push('  exported PES decodes back to the same stitches, colours and');
  lines.push('  dimensions it was generated from.');
  lines.push('');
  lines.push('  NOT established: that this file sews correctly on physical');
  lines.push(`  hardware. No ${machine.name} compatibility claim is made until a`);
  lines.push('  stitch-out is run and recorded by an operator.');
  return lines.join('\n');
}
