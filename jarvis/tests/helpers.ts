/**
 * Test scaffolding.
 *
 * `createTestJarvis` builds a complete, real JARVIS — real SQLite (in memory),
 * real registry, real executor, real permission policy — with a scripted model
 * provider standing in for the network. Nothing under test is mocked except the
 * model itself, which is exactly the boundary we do not control.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createJarvis, type Jarvis, type TurnContext } from '@jarvis/core';
import { loadConfig, type ChatMessage, type JarvisConfig, type ProviderStatus, type ToolCall } from '@jarvis/shared';
import type { ChatRequest, ChatResponse, ModelProvider } from '@jarvis/providers';

/** One scripted model turn: either plain text, or a set of tool calls. */
export interface ScriptedTurn {
  content?: string;
  toolCalls?: { name: string; arguments: Record<string, unknown> }[];
}

/**
 * A model provider driven by a script.
 *
 * It records every request it received so tests can assert on what the
 * orchestrator actually sent — the system prompt, the tool specs, the message
 * history after a tool result.
 */
export class ScriptedProvider implements ModelProvider {
  readonly id = 'scripted';
  readonly model = 'scripted-model-v1';
  readonly requests: ChatRequest[] = [];
  private turns: ScriptedTurn[];
  private index = 0;
  private available: boolean;
  private failWith: string | null = null;

  constructor(turns: ScriptedTurn[] = [], options: { available?: boolean } = {}) {
    this.turns = turns;
    this.available = options.available ?? true;
  }

  script(turns: ScriptedTurn[]): this {
    this.turns = turns;
    this.index = 0;
    return this;
  }

  failNext(message: string): this {
    this.failWith = message;
    return this;
  }

  setAvailable(available: boolean): this {
    this.available = available;
    return this;
  }

  isAvailable(): boolean {
    return this.available;
  }

  status(): ProviderStatus {
    const status: ProviderStatus = {
      id: this.id,
      kind: 'model',
      available: this.available,
      model: this.model,
    };
    if (!this.available) status.reason = 'scripted provider marked unavailable for this test';
    return status;
  }

  async chat(request: ChatRequest): Promise<ChatResponse> {
    this.requests.push(request);
    if (this.failWith) {
      const message = this.failWith;
      this.failWith = null;
      throw new Error(message);
    }

    const turn = this.turns[this.index] ?? { content: '(script exhausted)' };
    this.index += 1;

    const toolCalls: ToolCall[] = (turn.toolCalls ?? []).map((call, i) => ({
      id: `call_${this.index}_${i}`,
      name: call.name,
      arguments: call.arguments,
    }));

    return {
      content: turn.content ?? '',
      toolCalls,
      finishReason: toolCalls.length > 0 ? 'tool_calls' : 'stop',
      model: this.model,
      providerId: this.id,
      latencyMs: 1,
    };
  }

  async healthCheck(): Promise<{ ok: boolean; detail: string }> {
    return { ok: this.available, detail: this.available ? 'scripted' : 'unavailable' };
  }

  lastRequest(): ChatRequest | undefined {
    return this.requests[this.requests.length - 1];
  }

  systemPromptOf(request: ChatRequest | undefined): string {
    return (request?.messages ?? []).find((m: ChatMessage) => m.role === 'system')?.content ?? '';
  }
}

/**
 * A standalone turn context, for tests that drive the runner or executor
 * directly rather than through the orchestrator (which makes its own).
 */
export function testTurn(turnId = 'turn_test'): TurnContext {
  return { turnId, signal: new AbortController().signal, parentTurnId: null };
}

export interface TestHarness {
  jarvis: Jarvis;
  provider: ScriptedProvider;
  config: JarvisConfig;
  workspace: string;
  userId: string;
  cleanup: () => void;
}

export function testConfig(overrides: Partial<JarvisConfig> = {}): JarvisConfig {
  const base = loadConfig({
    MODEL_PROVIDER: 'nvidia',
    NVIDIA_API_KEY: '',
    DATABASE_URL: ':memory:',
    APPROVAL_REQUIRED_LEVELS: 'EXTERNAL_ACTION,DESTRUCTIVE',
    APPROVAL_TIMEOUT_SECONDS: '900',
    SEARCH_PROVIDER: 'none',
  });
  return { ...base, ...overrides };
}

export function createTestJarvis(options: {
  turns?: ScriptedTurn[];
  config?: Partial<JarvisConfig>;
  provider?: ScriptedProvider;
} = {}): TestHarness {
  const workspace = mkdtempSync(join(tmpdir(), 'jarvis-test-'));
  const agentsDir = mkdtempSync(join(tmpdir(), 'jarvis-agents-'));
  const config = testConfig({ workspaceDir: workspace, ...options.config });
  const provider = options.provider ?? new ScriptedProvider(options.turns ?? []);

  const jarvis = createJarvis({ config, provider, databasePath: ':memory:', agentsDir });
  const userId = 'user_test';
  jarvis.store.users.ensure(userId);

  return {
    jarvis,
    provider,
    config,
    workspace,
    userId,
    cleanup: () => {
      jarvis.close();
      rmSync(workspace, { recursive: true, force: true });
      rmSync(agentsDir, { recursive: true, force: true });
    },
  };
}
