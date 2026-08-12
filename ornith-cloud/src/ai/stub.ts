import type { AIProvider, AIRequest, AIStreamHandlers, ProviderError } from './types.js';

export interface StubOptions {
  /** Chunks emitted in order. Defaults to a short greeting. */
  script?: string[];
  /** Delay between chunks. */
  delayMs?: number;
  /** When set, fail with this error instead of streaming. */
  failWith?: ProviderError;
  /** Never emit and never resolve until aborted, to exercise timeouts. */
  hang?: boolean;
}

/**
 * Deterministic provider for tests. Records the request it was given so tests
 * can assert on the assembled prompt — particularly that retrieved content was
 * fenced as untrusted.
 */
export function createStubProvider(options: StubOptions = {}): AIProvider & {
  lastRequest: AIRequest | null;
} {
  const provider = {
    name: 'stub',
    lastRequest: null as AIRequest | null,

    async streamChat(request: AIRequest, handlers: AIStreamHandlers): Promise<void> {
      provider.lastRequest = request;

      if (options.failWith) {
        handlers.onError(options.failWith);
        return;
      }

      if (options.hang) {
        await new Promise<void>((resolve) => {
          if (request.signal?.aborted) return resolve();
          request.signal?.addEventListener('abort', () => resolve(), { once: true });
        });
        return;
      }

      const script = options.script ?? ['Hello', ' from', ' the', ' stub', ' provider.'];
      for (const chunk of script) {
        if (request.signal?.aborted) return;
        if (options.delayMs) await new Promise((r) => setTimeout(r, options.delayMs));
        handlers.onDelta(chunk);
      }

      handlers.onDone({ inputTokens: 10, outputTokens: script.length });
    },
  };

  return provider;
}
