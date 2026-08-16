import { describe, it, expect } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PermissionPolicy, PathEscapeError, resolveWorkspacePath } from '@jarvis/security';
import { AGENT_DEFINITIONS, JARVIS_AGENT } from '@jarvis/agents';
import { validateInput } from '@jarvis/shared';
import type { ToolDefinition } from '@jarvis/shared';

const tool = (name: string, risk: ToolDefinition['risk'], requiresApproval = false) => ({
  name,
  risk,
  requiresApproval,
});

describe('permission policy', () => {
  const policy = new PermissionPolicy(['EXTERNAL_ACTION', 'DESTRUCTIVE']);

  it('requires approval for EXTERNAL_ACTION and DESTRUCTIVE only', () => {
    expect(policy.requiresApprovalForRisk('READ')).toBe(false);
    expect(policy.requiresApprovalForRisk('WRITE')).toBe(false);
    expect(policy.requiresApprovalForRisk('EXTERNAL_ACTION')).toBe(true);
    expect(policy.requiresApprovalForRisk('DESTRUCTIVE')).toBe(true);
  });

  it('lets a tool opt in to approval but never out of it', () => {
    expect(policy.approvalFor(tool('file_write', 'WRITE', true)).required).toBe(true);
    // A DESTRUCTIVE tool declaring requiresApproval:false is still gated.
    expect(policy.approvalFor(tool('rm', 'DESTRUCTIVE', false)).required).toBe(true);
    expect(policy.approvalFor(tool('web_search', 'READ', false)).required).toBe(false);
  });

  it('honours a stricter configured policy', () => {
    const strict = new PermissionPolicy(['WRITE', 'EXTERNAL_ACTION', 'DESTRUCTIVE']);
    expect(strict.approvalFor(tool('memory_write', 'WRITE')).required).toBe(true);
  });

  describe('agent capability', () => {
    it('scout is read-only and cannot use write tools', () => {
      const scout = AGENT_DEFINITIONS.scout;
      expect(policy.canAgentUseTool(scout, tool('web_search', 'READ')).allowed).toBe(true);
      expect(policy.canAgentUseTool(scout, tool('memory_write', 'WRITE')).allowed).toBe(false);
      expect(policy.canAgentUseTool(scout, tool('file_delete', 'DESTRUCTIVE')).allowed).toBe(false);
    });

    it('refuses a tool that is not on the agent’s list even at a permitted risk', () => {
      const scout = AGENT_DEFINITIONS.scout;
      const decision = policy.canAgentUseTool(scout, tool('some_other_read_tool', 'READ'));
      expect(decision.allowed).toBe(false);
      expect(decision.reason).toMatch(/not permitted/);
    });

    it('developer may write but may not delete', () => {
      const developer = AGENT_DEFINITIONS.developer;
      expect(policy.canAgentUseTool(developer, tool('file_write', 'WRITE')).allowed).toBe(true);
      expect(policy.canAgentUseTool(developer, tool('file_delete', 'DESTRUCTIVE')).allowed).toBe(false);
    });

    it('advisor may not perform destructive actions', () => {
      const advisor = AGENT_DEFINITIONS.advisor;
      expect(policy.canAgentUseTool(advisor, tool('file_delete', 'DESTRUCTIVE')).allowed).toBe(false);
      expect(policy.canAgentUseTool(advisor, tool('task_create', 'WRITE')).allowed).toBe(true);
    });

    it('operator may use destructive tools, still subject to approval', () => {
      const operator = AGENT_DEFINITIONS.operator;
      expect(policy.canAgentUseTool(operator, tool('file_delete', 'DESTRUCTIVE')).allowed).toBe(true);
      expect(policy.approvalFor(tool('file_delete', 'DESTRUCTIVE')).required).toBe(true);
    });

    it('the orchestrator’s wildcard grants tool access but not an approval bypass', () => {
      expect(policy.canAgentUseTool(JARVIS_AGENT, tool('anything', 'DESTRUCTIVE')).allowed).toBe(true);
      expect(policy.approvalFor(tool('anything', 'DESTRUCTIVE')).required).toBe(true);
    });
  });
});

describe('workspace path sandbox', () => {
  const root = mkdtempSync(join(tmpdir(), 'jarvis-sandbox-'));

  it('resolves ordinary relative paths inside the root', () => {
    expect(resolveWorkspacePath(root, 'notes/todo.md')).toBe(join(root, 'notes/todo.md'));
  });

  it('rejects traversal, absolute paths and null bytes', () => {
    for (const bad of [
      '../secrets.txt',
      '../../etc/passwd',
      'notes/../../escape.txt',
      '/etc/passwd',
      'a\0b',
      '',
    ]) {
      expect(() => resolveWorkspacePath(root, bad), bad).toThrow(PathEscapeError);
    }
  });

  it('rejects a path that resolves to the root itself', () => {
    expect(() => resolveWorkspacePath(root, '.')).toThrow(PathEscapeError);
  });

  rmSync(root, { recursive: true, force: true });
});

describe('tool input validation', () => {
  const schema = {
    type: 'object' as const,
    properties: {
      query: { type: 'string' as const, minLength: 2 },
      limit: { type: 'integer' as const, minimum: 1, maximum: 10, default: 5 },
      mode: { type: 'string' as const, enum: ['fast', 'thorough'] },
    },
    required: ['query'],
    additionalProperties: false,
  };

  it('accepts valid input and applies defaults', () => {
    const result = validateInput({ query: 'jarvis' }, schema);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value).toEqual({ query: 'jarvis', limit: 5 });
  });

  it('rejects missing required fields', () => {
    const result = validateInput({}, schema);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors[0]).toMatch(/query: required/);
  });

  it('rejects wrong types, out-of-range numbers and bad enums', () => {
    expect(validateInput({ query: 5 }, schema).ok).toBe(false);
    expect(validateInput({ query: 'ok', limit: 99 }, schema).ok).toBe(false);
    expect(validateInput({ query: 'ok', mode: 'sideways' }, schema).ok).toBe(false);
    expect(validateInput({ query: 'x' }, schema).ok).toBe(false); // minLength
  });

  it('rejects unexpected properties when additionalProperties is false', () => {
    const result = validateInput({ query: 'ok', sneaky: true }, schema);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.join()).toMatch(/unexpected property/);
  });

  it('rejects a non-object argument payload', () => {
    expect(validateInput('just a string', schema).ok).toBe(false);
  });
});
