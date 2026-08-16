/**
 * A minimal MCP client, stdio transport.
 *
 * MCP over stdio is JSON-RPC 2.0 in newline-delimited JSON. That is small
 * enough to write out, and writing it out is the better trade here: the
 * official SDK pulls express, hono, jose, ajv, cors and rate-limiting — mostly
 * the *server* half — into the one process that holds every credential in the
 * system. This runtime deliberately has two runtime dependencies; adding
 * seventeen to talk to an untrusted peer is the wrong direction.
 *
 * Scope: stdio only in v0.2. HTTP/SSE transport can be added behind this same
 * interface without touching the adapter or the registry.
 *
 * Everything this module receives is untrusted. It validates shapes, bounds
 * sizes, and never lets a remote payload decide control flow beyond the
 * request it answers.
 */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { truncate } from '@jarvis/shared';

export type McpConnectionState = 'disconnected' | 'connecting' | 'ready' | 'failed';

export interface McpToolDescriptor {
  /** The name the server used. Namespaced by JARVIS before registration. */
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface McpCallResult {
  /** Flattened text content. Data, never instructions. */
  text: string;
  /** Whether the server flagged the call as an error. */
  isError: boolean;
  /** Raw content blocks, bounded and shape-checked. */
  content: { type: string; text?: string }[];
}

export class McpProtocolError extends Error {
  readonly code = 'mcp_protocol_error';
  readonly server: string;
  constructor(server: string, message: string) {
    super(`mcp[${server}]: ${message}`);
    this.name = 'McpProtocolError';
    this.server = server;
  }
}

/** A remote payload larger than this is refused rather than buffered. */
const MAX_LINE_BYTES = 4 * 1024 * 1024;
const MAX_TOOLS = 200;
const MAX_DESCRIPTION = 4_000;
const MAX_RESULT_CHARS = 100_000;

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export interface McpClientOptions {
  id: string;
  command: string;
  args?: string[];
  env?: Record<string, string>;
  /** Per-request ceiling. The runtime's number, never the server's. */
  requestTimeoutMs?: number;
  /** Injected by tests. */
  spawnImpl?: typeof spawn;
  onStateChange?: (state: McpConnectionState, detail: string) => void;
}

export class McpClient {
  readonly id: string;
  private child: ChildProcessWithoutNullStreams | null = null;
  private state: McpConnectionState = 'disconnected';
  private detail = 'not connected';
  private nextId = 1;
  private pending = new Map<number, PendingCall>();
  private buffer = '';
  private readonly options: McpClientOptions;
  private readonly requestTimeoutMs: number;

  constructor(options: McpClientOptions) {
    this.options = options;
    this.id = options.id;
    this.requestTimeoutMs = options.requestTimeoutMs ?? 30_000;
  }

  get connectionState(): McpConnectionState {
    return this.state;
  }

  get connectionDetail(): string {
    return this.detail;
  }

  private setState(state: McpConnectionState, detail: string): void {
    this.state = state;
    this.detail = detail;
    this.options.onStateChange?.(state, detail);
  }

  /**
   * Start the server process and perform the MCP handshake.
   *
   * Never throws: a server that cannot start is a capability that is
   * unavailable, not a runtime failure. The state and detail say why.
   */
  async connect(): Promise<boolean> {
    if (this.state === 'ready') return true;
    this.setState('connecting', 'starting server process');

    try {
      const spawnFn = this.options.spawnImpl ?? spawn;
      this.child = spawnFn(this.options.command, this.options.args ?? [], {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, ...(this.options.env ?? {}) },
      }) as ChildProcessWithoutNullStreams;
    } catch (error) {
      this.setState('failed', `could not start: ${(error as Error).message}`);
      return false;
    }

    this.child.on('error', (error) => {
      this.failAll(`server process error: ${error.message}`);
      this.setState('failed', error.message);
    });

    this.child.on('exit', (code, signal) => {
      const reason = `server exited (code ${code ?? 'null'}, signal ${signal ?? 'none'})`;
      this.failAll(reason);
      // An unexpected disconnect is a state change, not a crash.
      this.setState('disconnected', reason);
      this.child = null;
    });

    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', (chunk: string) => this.onData(chunk));
    // stderr is the server's own logging. Never parsed, never executed.
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data', () => undefined);

    try {
      await this.request('initialize', {
        protocolVersion: '2024-11-05',
        capabilities: { tools: {} },
        clientInfo: { name: 'jarvis', version: '0.2.0' },
      });
      this.notify('notifications/initialized', {});
      this.setState('ready', 'connected');
      return true;
    } catch (error) {
      this.setState('failed', `handshake failed: ${(error as Error).message}`);
      this.shutdown();
      return false;
    }
  }

  shutdown(): void {
    this.failAll('client shut down');
    if (this.child) {
      this.child.kill();
      this.child = null;
    }
    if (this.state !== 'failed') this.setState('disconnected', 'shut down');
  }

  private failAll(reason: string): void {
    for (const [, call] of this.pending) {
      clearTimeout(call.timer);
      call.reject(new McpProtocolError(this.id, reason));
    }
    this.pending.clear();
  }

  /** Accumulate stdout and dispatch complete JSON-RPC lines. */
  private onData(chunk: string): void {
    this.buffer += chunk;

    // A server that never emits a newline must not be able to exhaust memory.
    if (this.buffer.length > MAX_LINE_BYTES) {
      this.buffer = '';
      this.failAll('server sent an oversized message');
      this.setState('failed', 'oversized message');
      this.shutdown();
      return;
    }

    let index = this.buffer.indexOf('\n');
    while (index >= 0) {
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (line) this.dispatch(line);
      index = this.buffer.indexOf('\n');
    }
  }

  private dispatch(line: string): void {
    let message: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(line);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return;
      message = parsed as Record<string, unknown>;
    } catch {
      // Malformed JSON from the peer is ignored, not fatal: it may be noise on
      // the same stream, and one bad line should not kill a working server.
      return;
    }

    const id = message.id;
    if (typeof id !== 'number') return; // notification or request from server — unused
    const call = this.pending.get(id);
    if (!call) return;

    this.pending.delete(id);
    clearTimeout(call.timer);

    if (message.error) {
      const error = message.error as { message?: string; code?: number };
      call.reject(
        new McpProtocolError(this.id, truncate(error.message ?? `error code ${error.code}`, 300)),
      );
      return;
    }
    call.resolve(message.result);
  }

  private notify(method: string, params: unknown): void {
    if (!this.child?.stdin.writable) return;
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`);
  }

  /**
   * One JSON-RPC request.
   *
   * The timeout is JARVIS's, not the server's: a remote peer cannot declare
   * that an operation may run indefinitely. `signal` additionally cancels on
   * the owning turn.
   */
  private request(method: string, params: unknown, signal?: AbortSignal): Promise<unknown> {
    return new Promise((resolve, reject) => {
      if (!this.child?.stdin.writable) {
        reject(new McpProtocolError(this.id, 'server is not connected'));
        return;
      }
      if (signal?.aborted) {
        reject(new McpProtocolError(this.id, 'cancelled before the request was sent'));
        return;
      }

      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new McpProtocolError(this.id, `request "${method}" timed out`));
      }, this.requestTimeoutMs);
      timer.unref?.();

      const onAbort = () => {
        this.pending.delete(id);
        clearTimeout(timer);
        // MCP's cancellation notification is best-effort; the runtime stops
        // waiting regardless of whether the peer honours it.
        this.notify('notifications/cancelled', { requestId: id, reason: 'turn cancelled' });
        reject(new McpProtocolError(this.id, 'cancelled'));
      };
      signal?.addEventListener('abort', onAbort, { once: true });

      this.pending.set(id, {
        resolve: (value) => {
          signal?.removeEventListener('abort', onAbort);
          resolve(value);
        },
        reject: (error) => {
          signal?.removeEventListener('abort', onAbort);
          reject(error);
        },
        timer,
      });

      try {
        this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
      } catch (error) {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(new McpProtocolError(this.id, `write failed: ${(error as Error).message}`));
      }
    });
  }

  /**
   * List the server's tools.
   *
   * Every descriptor is shape-checked and bounded before it leaves this method.
   * A malformed entry is dropped rather than registered, and a malformed list
   * yields nothing rather than throwing into the caller.
   */
  async listTools(): Promise<McpToolDescriptor[]> {
    const result = await this.request('tools/list', {});
    const tools = (result as { tools?: unknown })?.tools;
    if (!Array.isArray(tools)) {
      throw new McpProtocolError(this.id, 'tools/list did not return an array');
    }

    const descriptors: McpToolDescriptor[] = [];
    for (const entry of tools.slice(0, MAX_TOOLS)) {
      if (!entry || typeof entry !== 'object') continue;
      const candidate = entry as Record<string, unknown>;
      if (typeof candidate.name !== 'string' || !candidate.name.trim()) continue;

      const schema =
        candidate.inputSchema && typeof candidate.inputSchema === 'object' && !Array.isArray(candidate.inputSchema)
          ? (candidate.inputSchema as Record<string, unknown>)
          : { type: 'object', properties: {} };

      descriptors.push({
        name: candidate.name,
        description:
          typeof candidate.description === 'string'
            ? truncate(candidate.description, MAX_DESCRIPTION)
            : '',
        inputSchema: schema,
      });
    }
    return descriptors;
  }

  /** Invoke a remote tool. Results are flattened text; nothing is executed. */
  async callTool(
    name: string,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<McpCallResult> {
    const result = await this.request('tools/call', { name, arguments: args }, signal);
    if (!result || typeof result !== 'object') {
      throw new McpProtocolError(this.id, 'tools/call returned a malformed result');
    }

    const payload = result as { content?: unknown; isError?: unknown };
    const blocks: { type: string; text?: string }[] = [];

    if (Array.isArray(payload.content)) {
      for (const block of payload.content.slice(0, 100)) {
        if (!block || typeof block !== 'object') continue;
        const candidate = block as Record<string, unknown>;
        const entry: { type: string; text?: string } = {
          type: typeof candidate.type === 'string' ? candidate.type : 'unknown',
        };
        if (typeof candidate.text === 'string') entry.text = candidate.text;
        blocks.push(entry);
      }
    }

    const text = truncate(
      blocks
        .map((block) => block.text ?? `[${block.type}]`)
        .join('\n')
        .trim(),
      MAX_RESULT_CHARS,
    );

    return { text, isError: payload.isError === true, content: blocks };
  }
}
