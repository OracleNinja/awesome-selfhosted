import type { AgentDefinition, ToolDefinition, ToolInfo } from '@jarvis/shared';
import type { PermissionPolicy } from '@jarvis/security';
import type { ToolSpec } from '@jarvis/providers';

/**
 * The tool registry.
 *
 * Holds every tool JARVIS knows about and answers two questions:
 * "what exists?" and "what may *this agent* use?". The second question is
 * answered before the model is prompted — an agent's model never sees a tool
 * it is not allowed to call, which removes a whole class of prompt-injection
 * escalation.
 */
export class ToolRegistry {
  private tools = new Map<string, ToolDefinition>();

  constructor(private readonly policy: PermissionPolicy) {}

  register(tool: ToolDefinition): void {
    if (this.tools.has(tool.name)) {
      throw new Error(`tool "${tool.name}" is already registered`);
    }
    if (!/^[a-z][a-z0-9_]{1,63}$/.test(tool.name)) {
      throw new Error(
        `invalid tool name "${tool.name}": use lowercase letters, digits and underscores`,
      );
    }
    if (tool.inputSchema.type !== 'object') {
      throw new Error(`tool "${tool.name}" must declare an object input schema`);
    }
    this.tools.set(tool.name, tool);
  }

  registerAll(tools: ToolDefinition[]): void {
    for (const tool of tools) this.register(tool);
  }

  has(name: string): boolean {
    return this.tools.has(name);
  }

  get(name: string): ToolDefinition | undefined {
    return this.tools.get(name);
  }

  list(): ToolDefinition[] {
    return [...this.tools.values()].sort((a, b) => a.name.localeCompare(b.name));
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
    return this.list().map((tool) => {
      const state = availability(tool);
      const info: ToolInfo = {
        name: tool.name,
        description: tool.description,
        inputSchema: tool.inputSchema,
        risk: tool.risk,
        requiresApproval: this.policy.approvalFor(tool).required,
        available: state.available,
      };
      if (state.reason) info.unavailableReason = state.reason;
      return info;
    });
  }

  get size(): number {
    return this.tools.size;
  }
}
