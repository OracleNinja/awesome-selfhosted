/**
 * Unit handling.
 *
 * The internal coordinate system of the whole application is the same one used
 * by the Brother PEC/PES stitch formats:
 *
 *   - 1 unit = 0.1 mm
 *   - x increases to the right
 *   - y increases DOWNWARD (screen-like, which is what the file format stores)
 *
 * Keeping the internal representation identical to the export format removes a
 * whole class of sign/scale bugs at export time.
 */

/** Internal units per millimetre. */
export const UNITS_PER_MM = 10;

/** Internal units per inch. */
export const UNITS_PER_INCH = 254;

export const mmToUnits = (mm: number): number => mm * UNITS_PER_MM;
export const unitsToMm = (u: number): number => u / UNITS_PER_MM;
export const inchesToUnits = (inch: number): number => inch * UNITS_PER_INCH;
export const unitsToInches = (u: number): number => u / UNITS_PER_INCH;
export const mmToInches = (mm: number): number => mm / 25.4;
export const inchesToMm = (inch: number): number => inch * 25.4;

export type LengthUnit = 'mm' | 'in';

export function formatLength(units: number, unit: LengthUnit, digits = 2): string {
  return unit === 'mm'
    ? `${unitsToMm(units).toFixed(digits)} mm`
    : `${unitsToInches(units).toFixed(digits)} in`;
}
