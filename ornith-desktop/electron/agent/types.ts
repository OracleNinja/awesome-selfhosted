/**
 * Agent tool interfaces. V1 ships these definitions with NO implementations —
 * the registry is empty and the broker denies everything. They exist so that
 * adding filesystem and command tools later is additive rather than a rewrite,
 * and so the security model is fixed before any tool exists to abuse it.
 */

export interface WorkspaceContext {
  /** Canonical, symlink-resolved root. Every path argument must resolve inside it. */
  root: string;
}

export interface ToolCall {
  id: string;
  tool: string;
  args: Record<string, unknown>;
}

export type ToolRisk = 'read' | 'write' | 'execute';

export interface ToolDefinition<A = unknown, R = unknown> {
  name: string;
  description: string;
  /** JSON Schema: given to the model, and used for runtime validation. */
  parameters: object;
  risk: ToolRisk;
  /**
   * Human-readable preview built from the args alone. Must never call the model:
   * the user has to see the real command or diff, not a model-authored summary
   * of it, or approval means nothing.
   */
  preview(args: A, ctx: WorkspaceContext): string;
  execute(args: A, ctx: WorkspaceContext): Promise<R>;
}

export interface PermissionRequest {
  call: ToolCall;
  risk: ToolRisk;
  summary: string;
  preview: string;
}

export type PermissionDecision =
  | { grant: 'once' }
  | { grant: 'session'; scope: string }
  | { grant: 'always'; scope: string }
  | { deny: true };

export interface PermissionBroker {
  request(req: PermissionRequest): Promise<PermissionDecision>;
}

export interface ToolRegistry {
  list(): ToolDefinition[];
  get(name: string): ToolDefinition | undefined;
  register(tool: ToolDefinition): void;
}
