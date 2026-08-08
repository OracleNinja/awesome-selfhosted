/** Provider-neutral AI interface. Nothing above this layer names a vendor. */

export interface AIMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AIRequest {
  model: string;
  system: string;
  messages: AIMessage[];
  maxTokens: number;
  temperature?: number;
  /** Aborts the upstream request when the desktop cancels. */
  signal?: AbortSignal;
}

export interface AIUsage {
  inputTokens: number;
  outputTokens: number;
}

export interface AIStreamHandlers {
  onDelta: (text: string) => void;
  onDone: (usage: AIUsage) => void;
  onError: (error: ProviderError) => void;
}

export interface ProviderError {
  code:
    | 'PROVIDER_UNAVAILABLE'
    | 'PROVIDER_TIMEOUT'
    | 'PROVIDER_RATE_LIMITED'
    | 'PROVIDER_AUTH'
    | 'PROVIDER_BAD_RESPONSE';
  /** Safe for the desktop to display. Never contains keys or stack traces. */
  message: string;
  /** Logged server-side only. */
  detail?: string;
}

export interface AIProvider {
  readonly name: string;
  streamChat(request: AIRequest, handlers: AIStreamHandlers): Promise<void>;
}
