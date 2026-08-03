/** Validation result types shared by every validator in the application. */

export type Severity = 'INFO' | 'WARNING' | 'ERROR';

export interface ValidationIssue {
  id: string;
  severity: Severity;
  /** Short machine-readable category, e.g. `hoop.width`. */
  code: string;
  /** Operator-facing message. Must state the concrete measured value. */
  message: string;
  /** What the operator can do about it. */
  remedy?: string;
  /** Objects involved, when the issue is localised. */
  objectIds?: string[];
  /** Stitch index the issue points at, when relevant. */
  stitchIndex?: number;
}

export interface ValidationReport {
  issues: ValidationIssue[];
  /** True when there are no ERROR issues. */
  passed: boolean;
  errorCount: number;
  warningCount: number;
  infoCount: number;
  /** Timestamp the report was produced. */
  checkedAt: string;
}

export function buildReport(issues: ValidationIssue[]): ValidationReport {
  const errorCount = issues.filter((i) => i.severity === 'ERROR').length;
  const warningCount = issues.filter((i) => i.severity === 'WARNING').length;
  const infoCount = issues.filter((i) => i.severity === 'INFO').length;
  return {
    issues,
    passed: errorCount === 0,
    errorCount,
    warningCount,
    infoCount,
    checkedAt: new Date().toISOString(),
  };
}

export const SEVERITY_ORDER: Record<Severity, number> = { ERROR: 0, WARNING: 1, INFO: 2 };

export function sortIssues(issues: ValidationIssue[]): ValidationIssue[] {
  return [...issues].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}
