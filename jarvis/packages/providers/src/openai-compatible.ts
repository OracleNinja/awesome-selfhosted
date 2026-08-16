/**
 * Adapter for the OpenAI chat-completions wire format.
 *
 * This one implementation backs three configured providers — NVIDIA NIM
 * (build.nvidia.com and self-hosted), OpenAI itself, and any local runtime
 * that speaks the same protocol (vLLM, llama.cpp server, Ollama, TensorRT-LLM).
 * They differ only in identity, endpoint and default headers.
 */
import type { ChatMessage, ProviderStatus, ToolCall } from '@jarvis/shared';
import { fetchWithTimeout, id, safeHost, truncate } from '@jarvis/shared';
import {
  ProviderError,
  ProviderUnavailableError,
  type ChatRequest,
  type ChatResponse,
  type ModelProvider,
} from './types.ts';

export interface OpenAICompatibleOptions {
  id: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  /** Extra headers (NVIDIA wants none beyond auth; kept for gateways that do). */
  headers?: Record<string, string>;
  /** Whether an API key is mandatory — local runtimes often need none. */
  requiresApiKey?: boolean;
  timeoutMs?: number;
}

interface OpenAIToolCall {
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
}

interface OpenAIChoice {
  message?: {
    content?: string | null;
    tool_calls?: OpenAIToolCall[];
    reasoning_content?: string | null;
  };
  finish_reason?: string;
}

interface OpenAIResponse {
  choices?: OpenAIChoice[];
  usage?: { prompt_tokens?: number; completion_tokens?: number };
  model?: string;
  error?: { message?: string };
}

export function toOpenAIMessages(messages: ChatMessage[]): Record<string, unknown>[] {
  return messages.map((message) => {
    if (message.role === 'tool') {
      return {
        role: 'tool',
        tool_call_id: message.toolCallId ?? '',
        content: message.content,
      };
    }
    if (message.role === 'assistant' && message.toolCalls && message.toolCalls.length > 0) {
      return {
        role: 'assistant',
        content: message.content || null,
        tool_calls: message.toolCalls.map((call) => ({
          id: call.id,
          type: 'function',
          function: { name: call.name, arguments: JSON.stringify(call.arguments) },
        })),
      };
    }
    return { role: message.role, content: message.content };
  });
}

export function parseToolCalls(raw: OpenAIToolCall[] | undefined, providerId: string): ToolCall[] {
  if (!raw) return [];
  const calls: ToolCall[] = [];
  for (const item of raw) {
    const name = item.function?.name;
    if (!name) continue;
    let args: Record<string, unknown> = {};
    const rawArgs = item.function?.arguments;
    if (rawArgs && rawArgs.trim() !== '') {
      try {
        const parsed = JSON.parse(rawArgs);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          args = parsed as Record<string, unknown>;
        }
      } catch {
        // A model emitting malformed JSON is a model problem, not a crash.
        // Surface it as an argument the validator will reject with a clear error.
        args = { __unparsed_arguments: truncate(rawArgs, 500) };
      }
    }
    calls.push({ id: item.id || id(`${providerId}call`), name, arguments: args });
  }
  return calls;
}

export class OpenAICompatibleProvider implements ModelProvider {
  readonly id: string;
  readonly model: string;
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly requiresApiKey: boolean;
  private readonly timeoutMs: number;

  constructor(options: OpenAICompatibleOptions) {
    this.id = options.id;
    this.model = options.model;
    this.apiKey = options.apiKey;
    this.baseUrl = options.baseUrl.replace(/\/+$/, '');
    this.headers = options.headers ?? {};
    this.requiresApiKey = options.requiresApiKey ?? true;
    this.timeoutMs = options.timeoutMs ?? 120_000;
  }

  unavailableReason(): string | null {
    if (!this.baseUrl) return `${this.id}: base URL is not configured`;
    if (!this.model) return `${this.id}: model is not configured`;
    if (this.requiresApiKey && !this.apiKey) return `${this.id}: API key is not configured`;
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

    const body: Record<string, unknown> = {
      model: this.model,
      messages: toOpenAIMessages(request.messages),
      temperature: request.temperature ?? 0.4,
      max_tokens: request.maxTokens ?? 2048,
      stream: false,
    };
    if (request.tools && request.tools.length > 0) {
      body.tools = request.tools.map((tool) => ({
        type: 'function',
        function: {
          name: tool.name,
          description: tool.description,
          parameters: tool.parameters,
        },
      }));
      body.tool_choice = 'auto';
    }

    const startedAt = Date.now();
    let response: Response;
    try {
      response = await fetchWithTimeout(`${this.baseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          accept: 'application/json',
          ...(this.apiKey ? { authorization: `Bearer ${this.apiKey}` } : {}),
          ...this.headers,
        },
        body: JSON.stringify(body),
        timeoutMs: this.timeoutMs,
        ...(request.signal ? { signal: request.signal } : {}),
      });
    } catch (error) {
      throw new ProviderError(this.id, `request to ${safeHost(this.baseUrl)} failed: ${(error as Error).message}`, {
        retryable: true,
      });
    }

    const text = await response.text();
    if (!response.ok) {
      let detail = truncate(text, 400);
      try {
        const parsed = JSON.parse(text) as OpenAIResponse;
        if (parsed.error?.message) detail = truncate(parsed.error.message, 400);
      } catch {
        /* keep raw text */
      }
      throw new ProviderError(this.id, `HTTP ${response.status} from ${safeHost(this.baseUrl)}: ${detail}`, {
        status: response.status,
        retryable: response.status >= 500 || response.status === 429,
      });
    }

    let parsed: OpenAIResponse;
    try {
      parsed = JSON.parse(text) as OpenAIResponse;
    } catch {
      throw new ProviderError(this.id, `invalid JSON response: ${truncate(text, 200)}`);
    }

    const choice = parsed.choices?.[0];
    if (!choice) throw new ProviderError(this.id, 'response contained no choices');

    const toolCalls = parseToolCalls(choice.message?.tool_calls, this.id);
    const finish = choice.finish_reason;
    const result: ChatResponse = {
      content: choice.message?.content ?? '',
      toolCalls,
      finishReason:
        toolCalls.length > 0
          ? 'tool_calls'
          : finish === 'length'
            ? 'length'
            : 'stop',
      model: parsed.model ?? this.model,
      providerId: this.id,
      latencyMs: Date.now() - startedAt,
    };
    if (parsed.usage) {
      result.usage = {
        promptTokens: parsed.usage.prompt_tokens ?? 0,
        completionTokens: parsed.usage.completion_tokens ?? 0,
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
