/**
 * The MCP trust boundary.
 *
 * An MCP server is an untrusted capability *provider*. Being configured
 * establishes that JARVIS may talk to it; it establishes nothing about what its
 * tools are allowed to do. Everything a server sends — names, descriptions,
 * schemas, annotations, results — is untrusted input.
 *
 * What the server gets to decide:  what its tool is called remotely, what it
 *                                  claims to do, and what arguments it wants.
 * What JARVIS decides:             the capability's name in this runtime, its
 *                                  risk level, whether it needs approval, its
 *                                  timeout, and whether it may run at all.
 *
 * There is deliberately no field a server can set to lower its own risk or
 * waive approval, because there is no code path that reads one.
 */
import type {
  CapabilitySource,
  JsonSchema,
  McpServerConfig,
  RiskLevel,
  ToolDefinition,
  ToolResult,
} from '@jarvis/shared';
import { DEFAULT_TOOL_TIMEOUT_MS, RISK_ORDER, truncate } from '@jarvis/shared';
import { mcpCapabilityName } from '@jarvis/tools';
import type { McpClient, McpToolDescriptor } from './client.ts';

/**
 * Turn a remote schema into one the validator accepts.
 *
 * The remote schema is a *description*, and a wrong one must not become a
 * crash. Anything unrecognised is dropped rather than passed through, so a
 * hostile or broken schema cannot reach the validator in a shape it does not
 * expect. The tool still runs; its arguments are simply validated against
 * whatever survived.
 */
export function sanitizeSchema(raw: unknown, depth = 0): JsonSchema {
  if (depth > 8 || !raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { type: 'object', properties: {} };
  }
  const source = raw as Record<string, unknown>;

  const allowedTypes = ['object', 'string', 'number', 'integer', 'boolean', 'array', 'null'];
  const type =
    typeof source.type === 'string' && allowedTypes.includes(source.type)
      ? (source.type as JsonSchema['type'])
      : undefined;

  const schema: JsonSchema = {};
  // A top-level tool schema must be an object; the executor requires it.
  schema.type = depth === 0 ? 'object' : (type ?? 'string');

  if (typeof source.description === 'string') {
    schema.description = truncate(source.description, 500);
  }

  if (source.properties && typeof source.properties === 'object' && !Array.isArray(source.properties)) {
    const properties: Record<string, JsonSchema> = {};
    for (const [key, value] of Object.entries(source.properties as Record<string, unknown>).slice(0, 100)) {
      if (!/^[A-Za-z0-9_.-]{1,64}$/.test(key)) continue;
      properties[key] = sanitizeSchema(value, depth + 1);
    }
    schema.properties = properties;
  } else if (schema.type === 'object') {
    schema.properties = {};
  }

  if (Array.isArray(source.required)) {
    schema.required = source.required.filter((entry): entry is string => typeof entry === 'string').slice(0, 100);
  }
  if (source.items) schema.items = sanitizeSchema(source.items, depth + 1);
  if (Array.isArray(source.enum)) schema.enum = source.enum.slice(0, 100);
  if (typeof source.minimum === 'number') schema.minimum = source.minimum;
  if (typeof source.maximum === 'number') schema.maximum = source.maximum;
  if (typeof source.minLength === 'number') schema.minLength = source.minLength;
  if (typeof source.maxLength === 'number') schema.maxLength = source.maxLength;

  // additionalProperties is deliberately left permissive for remote tools: a
  // remote schema is often incomplete, and rejecting unlisted arguments would
  // break working servers. The risk floor, not the schema, is the control.

  return schema;
}

/** A remote name that could not form a safe capability name is refused. */
export function isUsableRemoteName(name: string): boolean {
  return /^[A-Za-z0-9_.-]{1,64}$/.test(name);
}

/**
 * Classify a remote tool's risk.
 *
 * JARVIS's decision, from configuration only. The server's description is never
 * consulted — a tool called `read_file` that actually deletes files would
 * otherwise classify itself as harmless.
 *
 * The floor defaults to EXTERNAL_ACTION because a remote capability reaches
 * outside this runtime by definition, which is exactly what that level means.
 * An operator may lower it per server, deliberately, in configuration.
 */
export function classifyRemoteRisk(server: McpServerConfig): RiskLevel {
  return server.riskFloor;
}

export interface McpAdapterDeps {
  server: McpServerConfig;
  client: McpClient;
}

/**
 * Build a JARVIS capability from a remote tool descriptor.
 *
 * The returned ToolDefinition is an ordinary tool: it goes through the same
 * registry, permission policy, approval gate, timeout boundary and audit log as
 * a local one. There is no MCP execution path.
 */
export function toolFromDescriptor(
  deps: McpAdapterDeps,
  descriptor: McpToolDescriptor,
): ToolDefinition | null {
  const { server, client } = deps;
  if (!isUsableRemoteName(descriptor.name)) return null;

  const name = mcpCapabilityName(server.id, descriptor.name.toLowerCase().replace(/[^a-z0-9_]/g, '_'));
  const risk = classifyRemoteRisk(server);

  // The description reaches the model, so it is bounded and clearly attributed.
  // The model may read it; the runtime never acts on it.
  const description =
    `[via MCP server "${server.id}"] ${descriptor.description || descriptor.name}`.slice(0, 1_000);

  return {
    name,
    description,
    inputSchema: sanitizeSchema(descriptor.inputSchema),
    risk,
    // Approval-gated whenever the risk floor says so. A server cannot opt out:
    // this is computed here, and the policy applies on top of it regardless.
    requiresApproval: RISK_ORDER[risk] >= RISK_ORDER.EXTERNAL_ACTION,
    timeoutMs: server.timeoutMs > 0 ? server.timeoutMs : DEFAULT_TOOL_TIMEOUT_MS,

    async execute(args, ctx): Promise<ToolResult> {
      if (client.connectionState !== 'ready') {
        return {
          ok: false,
          summary: `MCP server "${server.id}" is not connected.`,
          error: `server state: ${client.connectionState} (${client.connectionDetail})`,
        };
      }

      try {
        const result = await client.callTool(descriptor.name, args, ctx.signal);

        if (result.isError) {
          return {
            ok: false,
            summary: `${descriptor.name} reported an error.`,
            error: truncate(result.text || 'the server reported an error with no detail', 1_000),
          };
        }

        return {
          ok: true,
          summary: truncate(result.text || `${descriptor.name} returned no content.`, 300),
          // Returned content is data. It is handed to the model as a tool
          // result and never interpreted as an instruction by the runtime.
          data: { server: server.id, tool: descriptor.name, content: result.text },
        };
      } catch (error) {
        // A remote failure is this capability's failure, not the runtime's.
        return {
          ok: false,
          summary: `MCP call to "${descriptor.name}" failed.`,
          error: truncate((error as Error).message, 500),
        };
      }
    },
  };
}

export function mcpSource(server: McpServerConfig, remoteName: string): CapabilitySource {
  return { kind: 'mcp', server: server.id, remoteName };
}
