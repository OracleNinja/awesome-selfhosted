/**
 * MCP server lifecycle.
 *
 * Owns the configured servers, connects them, registers what they offer into
 * the capability registry, and — crucially — keeps a failing server's problems
 * local to that server. A broken MCP server makes its own capabilities
 * unavailable; it does not stop JARVIS from starting, answering, or running
 * anything else.
 */
import type { EventBus, McpServerConfig } from '@jarvis/shared';
import { truncate } from '@jarvis/shared';
import type { ToolRegistry } from '@jarvis/tools';
import { McpClient, type McpConnectionState } from './client.ts';
import { mcpSource, toolFromDescriptor } from './adapter.ts';

export interface McpServerStatus {
  id: string;
  transport: 'stdio';
  command: string;
  enabled: boolean;
  state: McpConnectionState;
  detail: string;
  /** Capability names registered from this server. */
  tools: string[];
  riskFloor: string;
  timeoutMs: number;
  /** Descriptors the server offered that JARVIS refused, with the reason. */
  rejected: { name: string; reason: string }[];
}

export interface McpManagerDeps {
  registry: ToolRegistry;
  events?: EventBus;
  /** Injected by tests to avoid spawning real processes. */
  createClient?: (server: McpServerConfig) => McpClient;
  userId?: string;
}

export class McpManager {
  private clients = new Map<string, McpClient>();
  private statuses = new Map<string, McpServerStatus>();

  constructor(
    private readonly servers: McpServerConfig[],
    private readonly deps: McpManagerDeps,
  ) {
    for (const server of servers) {
      this.statuses.set(server.id, {
        id: server.id,
        transport: server.transport,
        command: server.command,
        enabled: server.enabled,
        state: server.enabled ? 'disconnected' : 'disconnected',
        detail: server.enabled ? 'not started' : 'disabled in configuration',
        tools: [],
        riskFloor: server.riskFloor,
        timeoutMs: server.timeoutMs,
        rejected: [],
      });
    }
  }

  /**
   * Connect every enabled server and register its tools.
   *
   * Never throws. Each server is isolated: one that fails to start, hangs
   * during the handshake or returns nonsense leaves the others working.
   */
  async connectAll(): Promise<McpServerStatus[]> {
    await Promise.all(this.servers.filter((server) => server.enabled).map((server) => this.connect(server)));
    return this.status();
  }

  private async connect(server: McpServerConfig): Promise<void> {
    const status = this.statuses.get(server.id)!;

    const client =
      this.deps.createClient?.(server) ??
      new McpClient({
        id: server.id,
        command: server.command,
        args: server.args,
        env: server.env,
        requestTimeoutMs: server.timeoutMs,
        onStateChange: (state, detail) => this.onStateChange(server, state, detail),
      });
    this.clients.set(server.id, client);

    let connected = false;
    try {
      connected = await client.connect();
    } catch (error) {
      // connect() is documented not to throw; this is belt-and-braces so a
      // misbehaving transport still cannot take the runtime down.
      status.state = 'failed';
      status.detail = truncate((error as Error).message, 200);
      return;
    }

    if (!connected) {
      status.state = client.connectionState;
      status.detail = client.connectionDetail;
      return;
    }

    let descriptors;
    try {
      descriptors = await client.listTools();
    } catch (error) {
      status.state = 'failed';
      status.detail = `tools/list failed: ${truncate((error as Error).message, 200)}`;
      client.shutdown();
      return;
    }

    for (const descriptor of descriptors) {
      const tool = toolFromDescriptor({ server, client }, descriptor);
      if (!tool) {
        status.rejected.push({
          name: truncate(descriptor.name, 60),
          reason: 'unusable remote tool name',
        });
        continue;
      }
      try {
        this.deps.registry.register(tool, {
          source: mcpSource(server, descriptor.name),
        });
        status.tools.push(tool.name);
      } catch (error) {
        // A collision or an invalid shape is refused loudly for that one tool.
        status.rejected.push({
          name: truncate(descriptor.name, 60),
          reason: truncate((error as Error).message, 200),
        });
      }
    }

    status.state = 'ready';
    status.detail = `connected, ${status.tools.length} capabilit${status.tools.length === 1 ? 'y' : 'ies'}`;

    this.deps.events?.emit({
      type: 'ACTION_EXECUTED',
      userId: this.deps.userId ?? 'system',
      agent: 'system',
      summary: `MCP server "${server.id}" connected with ${status.tools.length} capabilities.`,
      data: { mcpServer: server.id, tools: status.tools, riskFloor: server.riskFloor },
    });
  }

  /**
   * React to a server changing state.
   *
   * A disconnect disables its capabilities rather than removing them: they stay
   * visible in the registry, reported as unavailable with the reason, so a
   * model asking for one gets a clear refusal instead of "no such tool".
   *
   * A server already judged `failed` keeps that verdict. Rejecting a server
   * means shutting its process down, and the disconnect that follows is a
   * consequence of the failure rather than new information — letting it
   * overwrite the status would replace "tools/list returned nonsense" with a
   * bare "disconnected" and lose the only explanation an operator has.
   */
  private onStateChange(server: McpServerConfig, state: McpConnectionState, detail: string): void {
    const status = this.statuses.get(server.id);
    if (!status) return;
    if (!(status.state === 'failed' && state === 'disconnected')) {
      status.state = state;
      status.detail = detail;
    }

    if (state === 'ready') {
      this.deps.registry.setSourceEnabled(server.id, true);
      return;
    }
    if (state === 'disconnected' || state === 'failed') {
      this.deps.registry.setSourceEnabled(
        server.id,
        false,
        `MCP server "${server.id}" is ${state}: ${truncate(detail, 160)}`,
      );
      this.deps.events?.emit({
        type: 'ERROR',
        userId: this.deps.userId ?? 'system',
        agent: 'system',
        summary: `MCP server "${server.id}" ${state}: ${truncate(detail, 120)}`,
        data: { kind: 'capability', mcpServer: server.id, state },
      });
    }
  }

  status(): McpServerStatus[] {
    return [...this.statuses.values()];
  }

  client(id: string): McpClient | undefined {
    return this.clients.get(id);
  }

  shutdown(): void {
    for (const client of this.clients.values()) client.shutdown();
    this.clients.clear();
  }
}
