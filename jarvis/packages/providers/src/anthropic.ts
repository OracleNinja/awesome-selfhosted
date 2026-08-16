/**
 * Anthropic provider — optional development / agent backend.
 *
 * Anthropic's Messages API differs from the OpenAI shape in three ways that
 * matter here: the system prompt is a top-level field, tool calls arrive as
 * `tool_use` content blocks, and tool results are `tool_result` blocks inside a
 * *user* message. This adapter absorbs all three so the orchestrator never
 * learns about them.
 */
import type { ChatMessage, ProviderStatus, ToolCall } from '@jarvis/shared';
import { fetchWithTimeout, safeHost, truncate } from '@jarvis/shared';
import type { ModelEndpointConfig } from '@jarvis/shared';
import {
  ProviderError,
  ProviderUnavailableError,
  type ChatRequest,
  type ChatResponse,
  type ModelProvider,
} from './types.ts';

const ANTHROPIC_VERSION = '2023-06-01';

interface AnthropicContentBlock {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
}

interface AnthropicResponse {
  content?: AnthropicContentBlock[];
  stop_reason?: string;
  model?: string;
  usage?: { input_tokens?: number; output_tokens?: number };
  error?: { message?: string };
}

interface AnthropicMessage {
  role: 'user' | 'assistant';
  content: unknown;
}

/** Split our flat message list into Anthropic's (system, messages) pair. */
export function toAnthropicMessages(messages: ChatMessage[]): {
  system: string;
  messages: AnthropicMessage[];
} {
  const systemParts: string[] = [];
  const out: AnthropicMessage[] = [];

  for (const message of messages) {
    if (message.role === 'system') {
      systemParts.push(message.content);
      continue;
    }

    if (message.role === 'tool') {
      const block = {
        type: 'tool_result',
        tool_use_id: message.toolCallId ?? '',
        content: message.content,
      };
      const last = out[out.length - 1];
      if (last && last.role === 'user' && Array.isArray(last.content)) {
        (last.content as unknown[]).push(block);
      } else {
        out.push({ role: 'user', content: [block] });
      }
      continue;
    }

    if (message.role === 'assistant' && message.toolCalls && message.toolCalls.length > 0) {
      const blocks: unknown[] = [];
      if (message.content) blocks.push({ type: 'text', text: message.content });
      for (const call of message.toolCalls) {
        blocks.push({ type: 'tool_use', id: call.id, name: call.name, input: call.arguments });
      }
      out.push({ role: 'assistant', content: blocks });
      continue;
    }

    out.push({ role: message.role, content: message.content });
  }

  return { system: systemParts.join('\n\n'), messages: out };
}

export class AnthropicProvider implements ModelProvider {
  readonly id = 'anthropic';
  readonly model: string;
  private readonly apiKey: string;
  private readonly baseUrl: string;

  constructor(config: ModelEndpointConfig) {
    this.apiKey = config.apiKey;
    this.baseUrl = (config.baseUrl || 'https://api.anthropic.com').replace(/\/+$/, '');
    this.model = config.model;
  }

  private unavailableReason(): string | null {
    if (!this.apiKey) return 'anthropic: ANTHROPIC_API_KEY is not configured';
    if (!this.model) return 'anthropic: ANTHROPIC_MODEL is not configured';
    return null;
  }

  isAvailable(): boolean {
    return this.unavailableReason() === null;
  }

  status(): ProviderStatus {
    const reason = this.unavailableReason();
    const status: ProviderStatus = {
      id: this.id,
      kind: 'model',
      available: reason === null,
      model: this.model,
      endpoint: safeHost(this.baseUrl),
    };
    if (reason) status.reason = reason;
    return status;
  }

  async chat(request: ChatRequest): Promise<ChatResponse> {
    const reason = this.unavailableReason();
    if (reason) throw new ProviderUnavailableError(this.id, reason);

    const { system, messages } = toAnthropicMessages(request.messages);
    const body: Record<string, unknown> = {
      model: this.model,
      max_tokens: request.maxTokens ?? 2048,
      temperature: request.temperature ?? 0.4,
      messages,
    };
    if (system) body.system = system;
    if (request.tools && request.tools.length > 0) {
      body.tools = request.tools.map((tool) => ({
        name: tool.name,
        description: tool.description,
        input_schema: tool.parameters,
      }));
    }

    const startedAt = Date.now();
    let response: Response;
    try {
      response = await fetchWithTimeout(`${this.baseUrl}/v1/messages`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-api-key': this.apiKey,
          'anthropic-version': ANTHROPIC_VERSION,
        },
        body: JSON.stringify(body),
        timeoutMs: 120_000,
        ...(request.signal ? { signal: request.signal } : {}),
      });
    } catch (error) {
      throw new ProviderError(this.id, `request failed: ${(error as Error).message}`, {
        retryable: true,
      });
    }

    const text = await response.text();
    if (!response.ok) {
      let detail = truncate(text, 400);
      try {
        const parsed = JSON.parse(text) as AnthropicResponse;
        if (parsed.error?.message) detail = truncate(parsed.error.message, 400);
      } catch {
        /* keep raw text */
      }
      throw new ProviderError(this.id, `HTTP ${response.status}: ${detail}`, {
        status: response.status,
        retryable: response.status >= 500 || response.status === 429,
      });
    }

    const parsed = JSON.parse(text) as AnthropicResponse;
    const textParts: string[] = [];
    const toolCalls: ToolCall[] = [];
    for (const block of parsed.content ?? []) {
      if (block.type === 'text' && block.text) textParts.push(block.text);
      if (block.type === 'tool_use' && block.name) {
        toolCalls.push({
          id: block.id ?? `anthropic_${toolCalls.length}`,
          name: block.name,
          arguments: block.input ?? {},
        });
      }
    }

    const result: ChatResponse = {
      content: textParts.join('\n'),
      toolCalls,
      finishReason:
        toolCalls.length > 0 ? 'tool_calls' : parsed.stop_reason === 'max_tokens' ? 'length' : 'stop',
      model: parsed.model ?? this.model,
      providerId: this.id,
      latencyMs: Date.now() - startedAt,
    };
    if (parsed.usage) {
      result.usage = {
        promptTokens: parsed.usage.input_tokens ?? 0,
        completionTokens: parsed.usage.output_tokens ?? 0,
      };
    }
    return result;
  }

  async healthCheck(): Promise<{ ok: boolean; detail: string }> {
    const reason = this.unavailableReason();
    if (reason) return { ok: false, detail: reason };
    try {
      const response = await this.chat({
        messages: [{ role: 'user', content: 'ping' }],
        maxTokens: 8,
        temperature: 0,
      });
      return { ok: true, detail: `${response.model} responded in ${response.latencyMs}ms` };
    } catch (error) {
      return { ok: false, detail: (error as Error).message };
    }
  }
}
