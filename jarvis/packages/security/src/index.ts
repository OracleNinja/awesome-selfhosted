/**
 * The permission model.
 *
 * Two independent gates guard every tool invocation, and both must pass:
 *
 *   1. Agent capability — may this agent use this tool at all? Enforced before
 *      the tool list is even shown to the model, so a model cannot request a
 *      tool its agent does not have.
 *   2. Approval — does this risk level require a human decision? Enforced at
 *      execution time by the executor, which refuses to run anything that
 *      needs approval and has not received one.
 *
 * Neither gate consults the model. There is no prompt, no argument and no tool
 * result that can raise an agent's ceiling or waive an approval: the policy is
 * evaluated in code from the tool's declared risk and the agent's declared
 * limits.
 */
import { RISK_ORDER, type AgentDefinition, type RiskLevel, type ToolDefinition } from '@jarvis/shared';
import { resolve, relative, isAbsolute, sep } from 'node:path';

export interface PermissionDecision {
  allowed: boolean;
  reason: string;
}

export interface ApprovalDecision {
  required: boolean;
  reason: string;
}

export class PermissionPolicy {
  readonly requiredLevels: Set<RiskLevel>;

  constructor(requiredLevels: RiskLevel[] = ['EXTERNAL_ACTION', 'DESTRUCTIVE']) {
    this.requiredLevels = new Set(requiredLevels);
  }

  /** Does an action at this risk level need a human decision? */
  requiresApprovalForRisk(risk: RiskLevel): boolean {
    return this.requiredLevels.has(risk);
  }

  /**
   * Effective approval requirement for a tool.
   *
   * A tool may opt *in* to approval by declaring `requiresApproval: true`, but
   * it can never opt out of a policy-mandated level.
   */
  approvalFor(tool: Pick<ToolDefinition, 'name' | 'risk' | 'requiresApproval'>): ApprovalDecision {
    if (this.requiresApprovalForRisk(tool.risk)) {
      return {
        required: true,
        reason: `policy requires approval for ${tool.risk} actions`,
      };
    }
    if (tool.requiresApproval) {
      return { required: true, reason: `tool "${tool.name}" always requires approval` };
    }
    return { required: false, reason: `${tool.risk} actions do not require approval` };
  }

  /** May this agent invoke this tool? */
  canAgentUseTool(
    agent: AgentDefinition,
    tool: Pick<ToolDefinition, 'name' | 'risk'>,
  ): PermissionDecision {
    const allowsAll = agent.allowedTools.length === 1 && agent.allowedTools[0] === '*';
    if (!allowsAll && !agent.allowedTools.includes(tool.name)) {
      return {
        allowed: false,
        reason: `agent "${agent.name}" is not permitted to use tool "${tool.name}"`,
      };
    }
    if (RISK_ORDER[tool.risk] > RISK_ORDER[agent.maxRisk]) {
      return {
        allowed: false,
        reason: `tool "${tool.name}" is ${tool.risk}; agent "${agent.name}" is limited to ${agent.maxRisk}`,
      };
    }
    if (agent.readOnly && RISK_ORDER[tool.risk] > RISK_ORDER.READ) {
      return {
        allowed: false,
        reason: `agent "${agent.name}" is read-only and cannot use ${tool.risk} tool "${tool.name}"`,
      };
    }
    return { allowed: true, reason: 'permitted' };
  }
}

export class PermissionDeniedError extends Error {
  readonly code = 'permission_denied';
  constructor(message: string) {
    super(message);
    this.name = 'PermissionDeniedError';
  }
}

export class ApprovalRequiredError extends Error {
  readonly code = 'approval_required';
  readonly approvalId: string;
  constructor(approvalId: string, message: string) {
    super(message);
    this.name = 'ApprovalRequiredError';
    this.approvalId = approvalId;
  }
}

// ---------------------------------------------------------------------------
// Filesystem sandbox
// ---------------------------------------------------------------------------

export class PathEscapeError extends Error {
  readonly code = 'path_escape';
  constructor(message: string) {
    super(message);
    this.name = 'PathEscapeError';
  }
}

/**
 * Resolve a user/model supplied path inside the workspace root.
 *
 * Rejects absolute paths and any traversal that lands outside the root. The
 * check is done on the *resolved* path, so `a/../../etc/passwd`, `..%2f` after
 * decoding, and symlink-free traversal all fail the same way.
 */
export function resolveWorkspacePath(workspaceRoot: string, requested: string): string {
  if (!requested || requested.trim() === '') {
    throw new PathEscapeError('path must not be empty');
  }
  if (isAbsolute(requested)) {
    throw new PathEscapeError(`absolute paths are not allowed: ${requested}`);
  }
  if (requested.includes('\0')) {
    throw new PathEscapeError('path contains a null byte');
  }

  const root = resolve(workspaceRoot);
  const target = resolve(root, requested);
  const rel = relative(root, target);

  if (rel === '' || rel.startsWith('..') || rel.split(sep).includes('..')) {
    throw new PathEscapeError(
      `path escapes the workspace directory: ${requested}`,
    );
  }
  return target;
}
