/**
 * Machine and hoop profiles.
 *
 * Limits here come from the published specification of each machine. Where a
 * limit is not published, the field is left `null` rather than guessed, and the
 * validator skips the corresponding check instead of inventing a threshold.
 */

import { mmToUnits } from './units';

export interface Hoop {
  id: string;
  name: string;
  /** Full hoop embroidery field width in 0.1 mm units. */
  width: number;
  /** Full hoop embroidery field height in 0.1 mm units. */
  height: number;
  /**
   * Margin kept clear of the hoop edge, in 0.1 mm units. Stitches inside the
   * hoop but within this margin are flagged as a warning, not an error.
   */
  safetyMargin: number;
}

export interface MachineProfile {
  id: string;
  name: string;
  manufacturer: string;
  hoops: Hoop[];
  defaultHoopId: string;
  /** Maximum stitches per design, or null when the machine publishes no limit. */
  maxStitchCount: number | null;
  /** Maximum colour changes per design, or null when unlimited/unpublished. */
  maxColorChanges: number | null;
  /** Number of needles. 1 means the operator re-threads at every colour change. */
  needles: number;
  supportedFormats: string[];
  /** Longest single stitch the machine will sew, in 0.1 mm units. */
  maxStitchLength: number;
  /** Palette the machine displays colours from. */
  threadChartId: string;
  notes: string;
}

export const BROTHER_SE700: MachineProfile = {
  id: 'brother-se700',
  name: 'Brother SE700',
  manufacturer: 'Brother',
  hoops: [
    {
      id: 'se700-5x7',
      name: '5" x 7" (130 x 180 mm)',
      width: mmToUnits(130),
      height: mmToUnits(180),
      safetyMargin: mmToUnits(2),
    },
    {
      id: 'se700-4x4',
      name: '4" x 4" (100 x 100 mm)',
      width: mmToUnits(100),
      height: mmToUnits(100),
      safetyMargin: mmToUnits(2),
    },
  ],
  defaultHoopId: 'se700-5x7',
  maxStitchCount: null,
  maxColorChanges: null,
  needles: 1,
  supportedFormats: ['PES', 'PEC', 'DST'],
  maxStitchLength: mmToUnits(12.7),
  threadChartId: 'brother-pec',
  notes:
    'Single-needle home machine with a 5" x 7" maximum embroidery field. Reads PES from USB. ' +
    'Brother does not publish a hard maximum stitch count for this model, so no stitch-count ' +
    'error is raised; a production warning is raised at high counts instead.',
};

/**
 * A generic 4"x4" single-needle profile, useful when the operator is unsure of
 * the exact model but knows the hoop.
 */
export const GENERIC_4X4: MachineProfile = {
  id: 'generic-4x4',
  name: 'Generic 4" x 4" single needle',
  manufacturer: 'Generic',
  hoops: [
    {
      id: 'generic-4x4-hoop',
      name: '4" x 4" (100 x 100 mm)',
      width: mmToUnits(100),
      height: mmToUnits(100),
      safetyMargin: mmToUnits(2),
    },
  ],
  defaultHoopId: 'generic-4x4-hoop',
  maxStitchCount: null,
  maxColorChanges: null,
  needles: 1,
  supportedFormats: ['PES', 'DST'],
  maxStitchLength: mmToUnits(12.7),
  threadChartId: 'brother-pec',
  notes: 'Conservative fallback profile. Verify against your machine manual before production.',
};

export const machineProfiles: MachineProfile[] = [BROTHER_SE700, GENERIC_4X4];

export function getMachine(id: string): MachineProfile | undefined {
  return machineProfiles.find((m) => m.id === id);
}

export function getHoop(machine: MachineProfile, hoopId: string): Hoop | undefined {
  return machine.hoops.find((h) => h.id === hoopId);
}

export function defaultHoop(machine: MachineProfile): Hoop {
  return getHoop(machine, machine.defaultHoopId) ?? machine.hoops[0];
}

/** Register an additional machine profile at runtime. */
export function registerMachine(profile: MachineProfile): void {
  const i = machineProfiles.findIndex((m) => m.id === profile.id);
  if (i >= 0) machineProfiles[i] = profile;
  else machineProfiles.push(profile);
}
