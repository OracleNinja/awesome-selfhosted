import type {
  AgentDefinition,
  CapabilityRecord,
  CapabilitySource,
  ToolDefinition,
  ToolInfo,
} from '@jarvis/shared';
import { DEFAULT_TOOL_TIMEOUT_MS, now } from '@jarvis/shared';
import type { PermissionPolicy } from '@jarvis/security';
import type { ToolSpec } from '@jarvis/providers';

/** Namespace prefix for capabilities supplied by an MCP server. */
export const MCP_NAMESPACE = 'mcp';

/**
 * The canonical name for an MCP capability.
 *
 * Assigned by JARVIS from the *configured* server id and the remote tool name,
 * so a remote server cannot choose a name that shadows a built-in: every remote
 * capability is structurally confined to the `mcp__<server>__` namespace.
 */
export function mcpCapabilityName(server: string, remoteName: string): string {
  return `${MCP_NAMESPACE}__${server}__${remoteName}`;
}

export class CapabilityDisabledError extends Error {
  readonly code = 'capability_disabled';
  constructor(name: string, reason: string) {
    super(`capability "${name}" is disabled: ${reason}`);
    this.name = 'CapabilityDisabledError';
  }
}

/**
 * The capability registry.
 *
 * Holds every tool JARVIS knows about and answers two questions:
 * "what exists?" and "what may *this agent* use?". The second question is
 * answered before the model is prompted — an agent's model never sees a tool
 * it is not allowed to call, which removes a whole class of prompt-injection
 * escalation.
 */
export class ToolRegistry {
  private records = new Map<string, CapabilityRecord>();

  constructor(private readonly policy: PermissionPolicy) {}

  /**
   * Register a capability.
   *
   * `source` is supplied by the caller that knows where the capability came
   * from — the composition root for local tools, the MCP adapter for remote
   * ones. It is never read from the capability itself.
   *
   * Duplicate names are refused outright rather than overwritten: two providers
   * silently claiming the same capability is exactly the collision the
   * namespace exists to prevent, and the loud failure is the point.
   */
  register(
    tool: ToolDefinition,
    options: { source?: CapabilitySource; enabled?: boolean } = {},
  ): void {
    if (this.records.has(tool.name)) {
      throw new Error(`capability "${tool.name}" is already registered`);
    }
    if (!/^[a-z][a-z0-9_]{1,63}$/.test(tool.name)) {
      throw new Error(
        `invalid capability name "${tool.name}": use lowercase letters, digits and underscores`,
      );
    }
    if (tool.inputSchema.type !== 'object') {
      throw new Error(`capability "${tool.name}" must declare an object input schema`);
    }

    const source: CapabilitySource = options.source ?? { kind: 'local' };
    // Only the adapter may claim the mcp namespace, and everything it registers
    // must be inside it. This is what makes shadowing structurally impossible.
    if (source.kind === 'mcp' && tool.name !== mcpCapabilityName(source.server, source.remoteName)) {
      throw new Error(
        `MCP capability "${tool.name}" must be namespaced as ${mcpCapabilityName(source.server, source.remoteName)}`,
      );
    }
    if (source.kind === 'local' && tool.name.startsWith(`${MCP_NAMESPACE}__`)) {
      throw new Error(`local capability "${tool.name}" may not use the reserved mcp namespace`);
    }

    this.records.set(tool.name, {
      definition: tool,
      source,
      enabled: options.enabled ?? true,
      registeredAt: now(),
    });
  }

  registerAll(tools: ToolDefinition[], options: { source?: CapabilitySource } = {}): void {
    for (const tool of tools) this.register(tool, options);
  }

  /** Remove a capability — used when an MCP server disconnects. */
  unregister(name: string): boolean {
    return this.records.delete(name);
  }

  /** Remove every capability from one MCP server. Returns how many went. */
  unregisterSource(server: string): number {
    let removed = 0;
    for (const [name, record] of [...this.records]) {
      if (record.source.kind === 'mcp' && record.source.server === server) {
        this.records.delete(name);
        removed += 1;
      }
    }
    return removed;
  }

  /**
   * Enable or disable a capability.
   *
   * A disabled capability is hidden from models and refused by the executor —
   * it is not merely undocumented.
   */
  setEnabled(name: string, enabled: boolean, reason?: string): void {
    const record = this.records.get(name);
    if (!record) return;
    record.enabled = enabled;
    if (!enabled && reason) record.unavailableReason = reason;
    if (enabled) delete record.unavailableReason;
  }

  /** Disable every capability from one server, keeping them visible as unavailable. */
  setSourceEnabled(server: string, enabled: boolean, reason?: string): number {
    let changed = 0;
    for (const [name, record] of this.records) {
      if (record.source.kind === 'mcp' && record.source.server === server) {
        this.setEnabled(name, enabled, reason);
        changed += 1;
      }
    }
    return changed;
  }

  record(name: string): CapabilityRecord | undefined {
    return this.records.get(name);
  }

  /** Every registered capability, including disabled ones. */
  capabilities(): CapabilityRecord[] {
    return [...this.records.values()].sort((a, b) =>
      a.definition.name.localeCompare(b.definition.name),
    );
  }

  has(name: string): boolean {
    return this.records.has(name);
  }

  get(name: string): ToolDefinition | undefined {
    return this.records.get(name)?.definition;
  }

  /** Enabled capabilities only. Disabled ones are never offered to a model. */
  list(): ToolDefinition[] {
    return this.capabilities()
      .filter((record) => record.enabled)
      .map((record) => record.definition);
  }

  /** Why a capability cannot run, or null if it can. */
  disabledReason(name: string): string | null {
    const record = this.records.get(name);
    if (!record) return null;
    if (record.enabled) return null;
    return record.unavailableReason ?? 'capability is disabled';
  }

  /** Tools this agent is permitted to call, after policy filtering. */
  listForAgent(agent: AgentDefinition): ToolDefinition[] {
    return this.list().filter((tool) => this.policy.canAgentUseTool(agent, tool).allowed);
  }

  /** Model-facing tool specs for an agent. */
  specsForAgent(agent: AgentDefinition): ToolSpec[] {
    return this.listForAgent(agent).map((tool) => ({
      name: tool.name,
      description: this.describeForModel(tool),
      parameters: tool.inputSchema,
    }));
  }

  /**
   * Tell the model, in the tool description itself, when a call will pause for
   * human approval. Models that know this stop pretending the action completed.
   */
  private describeForModel(tool: ToolDefinition): string {
    const approval = this.policy.approvalFor(tool);
    const suffix = approval.required
      ? ` (risk: ${tool.risk}; requires human approval before it runs — do not assume it succeeded)`
      : ` (risk: ${tool.risk})`;
    return tool.description + suffix;
  }

  /** Serialisable list for the UI. Never includes `execute`. */
  infos(availability: (tool: ToolDefinition) => { available: boolean; reason?: string }): ToolInfo[] {
    return this.capabilities().map((record) => {
      const tool = record.definition;
      const state = record.enabled ? availability(tool) : { available: false, reason: this.disabledReason(tool.name) ?? undefined };
      const info: ToolInfo = {
        name: tool.name,
        description: tool.description,
        inputSchema: tool.inputSchema,
        risk: tool.risk,
        requiresApproval: this.policy.approvalFor(tool).required,
        timeoutMs: tool.timeoutMs ?? DEFAULT_TOOL_TIMEOUT_MS,
        available: state.available,
        source: record.source,
        enabled: record.enabled,
      };
      if (state.reason) info.unavailableReason = state.reason;
      return info;
    });
  }

  /** Count of enabled capabilities. */
  get size(): number {
    return this.list().length;
  }

  /** Count of everything registered, enabled or not. */
  get total(): number {
    return this.records.size;
  }
}
