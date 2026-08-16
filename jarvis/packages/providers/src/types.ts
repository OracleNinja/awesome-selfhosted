import type { ChatMessage, JsonSchema, ProviderStatus, ToolCall } from '@jarvis/shared';

/** Tool description handed to a model. Provider adapters translate this shape. */
export interface ToolSpec {
  name: string;
  description: string;
  parameters: JsonSchema;
}

export interface ChatRequest {
  messages: ChatMessage[];
  tools?: ToolSpec[];
  temperature?: number;
  maxTokens?: number;
  signal?: AbortSignal;
}

export interface ChatUsage {
  promptTokens: number;
  completionTokens: number;
}

export interface ChatResponse {
  content: string;
  toolCalls: ToolCall[];
  finishReason: 'stop' | 'tool_calls' | 'length' | 'error';
  model: string;
  providerId: string;
  usage?: ChatUsage;
  latencyMs: number;
}

/**
 * The contract every language model backend implements.
 *
 * JARVIS talks only to this interface. Swapping NVIDIA for Anthropic, an
 * OpenAI-compatible gateway or a future local GPU runtime is a configuration
 * change, never a code change in the orchestrator.
 */
export interface ModelProvider {
  readonly id: string;
  readonly model: string;
  /** False when required configuration (key/URL/model) is missing. */
  isAvailable(): boolean;
  status(): ProviderStatus;
  chat(request: ChatRequest): Promise<ChatResponse>;
  /** Cheap connectivity probe used by /api/system/health and the Settings view. */
  healthCheck(): Promise<{ ok: boolean; detail: string }>;
}

export class ProviderError extends Error {
  readonly providerId: string;
  readonly status: number;
  readonly retryable: boolean;

  constructor(
    providerId: string,
    message: string,
    options: { status?: number; retryable?: boolean } = {},
  ) {
    super(message);
    this.name = 'ProviderError';
    this.providerId = providerId;
    this.status = options.status ?? 502;
    this.retryable = options.retryable ?? false;
  }
}

/** Raised when a capability is requested that has not been configured. */
export class ProviderUnavailableError extends ProviderError {
  constructor(providerId: string, reason: string) {
    super(providerId, reason, { status: 503 });
    this.name = 'ProviderUnavailableError';
  }
}
