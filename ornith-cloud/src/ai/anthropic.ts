import Anthropic from '@anthropic-ai/sdk';
import type { AIProvider, AIRequest, AIStreamHandlers, ProviderError } from './types.js';

/**
 * Anthropic adapter.
 *
 * Written against the installed SDK (0.116.0), whose streaming surface is
 * `messages.create({ stream: true })` returning an async-iterable of
 * RawMessageStreamEvent. Text arrives as
 * { type: 'content_block_delta', delta: { type: 'text_delta', text } }
 * and final token counts as { type: 'message_delta', usage }.
 */
export function createAnthropicProvider(apiKey: string, timeoutMs: number): AIProvider {
  const client = new Anthropic({ apiKey, timeout: timeoutMs, maxRetries: 1 });

  return {
    name: 'anthropic',

    async streamChat(request: AIRequest, handlers: AIStreamHandlers): Promise<void> {
      let inputTokens = 0;
      let outputTokens = 0;

      try {
        const stream = await client.messages.create(
          {
            model: request.model,
            max_tokens: request.maxTokens,
            system: request.system,
            temperature: request.temperature,
            messages: request.messages.map((m) => ({ role: m.role, content: m.content })),
            stream: true,
          },
          { signal: request.signal },
        );

        for await (const event of stream) {
          if (event.type === 'message_start') {
            inputTokens = event.message.usage?.input_tokens ?? 0;
            continue;
          }
          if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
            handlers.onDelta(event.delta.text);
            continue;
          }
          if (event.type === 'message_delta') {
            outputTokens = event.usage?.output_tokens ?? outputTokens;
          }
        }

        handlers.onDone({ inputTokens, outputTokens });
      } catch (err) {
        if (request.signal?.aborted) return; // caller cancelled; not an error
        handlers.onError(classify(err));
      }
    },
  };
}

/** Maps SDK failures onto safe, displayable errors. Never leaks the key. */
export function classify(err: unknown): ProviderError {
  const status = (err as { status?: number })?.status;
  const raw = err instanceof Error ? err.message : String(err);
  // Defensive: an SDK message could in principle echo a header.
  const detail = raw.replace(/sk-[A-Za-z0-9_-]{8,}/g, '[redacted]').slice(0, 400);

  if (status === 401 || status === 403) {
    return {
      code: 'PROVIDER_AUTH',
      message: 'The online AI service rejected the server credentials.',
      detail,
    };
  }
  if (status === 429) {
    return {
      code: 'PROVIDER_RATE_LIMITED',
      message: 'The online AI service is rate limiting requests. Try again shortly.',
      detail,
    };
  }
  if (status === 408 || /timeout|timed out|aborted/i.test(raw)) {
    return {
      code: 'PROVIDER_TIMEOUT',
      message: 'The online AI service took too long to respond.',
      detail,
    };
  }
  if (typeof status === 'number' && status >= 500) {
    return {
      code: 'PROVIDER_UNAVAILABLE',
      message: 'The online AI service is temporarily unavailable.',
      detail,
    };
  }
  return {
    code: 'PROVIDER_BAD_RESPONSE',
    message: 'The online AI service returned an unexpected response.',
    detail,
  };
}
