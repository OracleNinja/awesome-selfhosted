import { Hono } from 'hono';
import { streamSSE } from 'hono/streaming';
import type { Config } from '../config.js';
import { log } from '../log.js';
import type { AIProvider } from '../ai/types.js';
import type { Retriever } from '../retrieval/pipeline.js';
import { classifyQuery } from '../routing/classify.js';
import { buildSystemPrompt, buildUserMessage } from '../routing/prompt.js';

export interface ChatDeps {
  config: Config;
  provider: AIProvider;
  retriever: Retriever;
  now?: () => number;
}

interface ChatBody {
  messages?: Array<{ role?: string; content?: string }>;
  web?: boolean;
  mode?: string;
}

/** Wire protocol, documented in README. */
type Event =
  | { type: 'status'; status: 'searching' | 'reading' | 'generating' }
  | { type: 'delta'; content: string }
  | { type: 'sources'; sources: Array<{ title: string; url: string; domain: string; cached: boolean }> }
  | { type: 'error'; code: string; message: string }
  | { type: 'complete'; timings: Record<string, number>; usage?: Record<string, number> };

const MAX_MESSAGES = 40;
const MAX_MESSAGE_CHARS = 24_000;

export function createChatRoute(deps: ChatDeps): Hono {
  const app = new Hono();
  const now = deps.now ?? (() => Date.now());

  app.post('/chat', async (c) => {
    const requestReceived = now();
    let body: ChatBody;

    try {
      body = (await c.req.json()) as ChatBody;
    } catch {
      return c.json({ code: 'BAD_REQUEST', message: 'Request body must be JSON.' }, 400);
    }

    const messages = (body.messages ?? [])
      .filter(
        (m): m is { role: 'user' | 'assistant'; content: string } =>
          (m?.role === 'user' || m?.role === 'assistant') && typeof m.content === 'string',
      )
      .slice(-MAX_MESSAGES)
      .map((m) => ({ role: m.role, content: m.content.slice(0, MAX_MESSAGE_CHARS) }));

    const last = messages.at(-1);
    if (!last || last.role !== 'user' || !last.content.trim()) {
      return c.json(
        { code: 'BAD_REQUEST', message: 'The last message must be a non-empty user message.' },
        400,
      );
    }

    const question = last.content;
    const classification = classifyQuery(question);
    const webAllowed = body.web !== false;
    const shouldRetrieve =
      webAllowed && classification.needsRetrieval && deps.config.searchProvider !== 'none';

    const model =
      classification.tier === 'fast' ? deps.config.fastModel : deps.config.onlineModel;

    log.info('gateway.request', {
      klass: classification.klass,
      reason: classification.reason,
      retrieve: shouldRetrieve,
      tier: classification.tier,
      messages: messages.length,
    });

    // The desktop closing the connection must cancel upstream work.
    const abort = new AbortController();
    c.req.raw.signal?.addEventListener('abort', () => abort.abort(), { once: true });

    return streamSSE(c, async (stream) => {
      // Writes are serialised through a chain rather than fired and forgotten.
      // The provider's onDelta/onError callbacks are synchronous, so an
      // un-awaited write can still be in flight when the callback returns —
      // and if the stream closes first, that event is silently lost.
      let writes: Promise<void> = Promise.resolve();

      const send = (event: Event): Promise<void> => {
        writes = writes.then(() =>
          stream.writeSSE({ event: event.type, data: JSON.stringify(event) }),
        );
        return writes;
      };

      const timings: Record<string, number> = {};
      let retrievalStarted = 0;

      try {
        let sources: Awaited<ReturnType<Retriever['retrieve']>>['sources'] = [];

        if (shouldRetrieve) {
          await send({ type: 'status', status: 'searching' });
          retrievalStarted = now();

          const result = await deps.retriever.retrieve(question, abort.signal);
          sources = result.sources;
          timings.searchMs = result.searchMs;
          timings.fetchMs = result.fetchMs;
          timings.retrievalMs = now() - retrievalStarted;

          if (sources.length > 0) {
            await send({ type: 'status', status: 'reading' });
            await send({
              type: 'sources',
              sources: sources.map((s) => ({
                title: s.title,
                url: s.url,
                domain: s.domain,
                cached: s.fromCache,
              })),
            });
          }
        }

        await send({ type: 'status', status: 'generating' });

        const history = messages.slice(0, -1);
        const aiMessages = [...history, { role: 'user' as const, content: buildUserMessage(question, sources) }];

        const modelStarted = now();
        let firstTokenAt = 0;
        let usage: Record<string, number> | undefined;
        let failed = false;

        await deps.provider.streamChat(
          {
            model,
            system: buildSystemPrompt(sources.length > 0, new Date(now()).toISOString()),
            messages: aiMessages,
            maxTokens: deps.config.maxOutputTokens,
            signal: abort.signal,
          },
          {
            onDelta: (text) => {
              if (!firstTokenAt) {
                firstTokenAt = now();
                timings.timeToFirstTokenMs = firstTokenAt - requestReceived;
                log.info('ai.first_token', { ms: firstTokenAt - modelStarted });
              }
              void send({ type: 'delta', content: text });
            },
            onDone: (u) => {
              usage = { inputTokens: u.inputTokens, outputTokens: u.outputTokens };
            },
            onError: (error) => {
              failed = true;
              log.warn('ai.error', { code: error.code, detail: error.detail });
              void send({ type: 'error', code: error.code, message: error.message });
            },
          },
        );

        if (!failed) {
          timings.generationMs = now() - modelStarted;
          timings.totalMs = now() - requestReceived;
          log.info('gateway.complete', { ...timings, model });
          await send({ type: 'complete', timings, usage });
        }

        // Drain anything still queued (deltas, a provider error) before the
        // stream closes.
        await writes;
      } catch (err) {
        // Anything unhandled becomes a structured error; never a stack trace.
        log.error('gateway.unhandled', {
          detail: err instanceof Error ? err.message.slice(0, 200) : String(err),
        });
        await send({
          type: 'error',
          code: 'INTERNAL',
          message: 'The online service failed to complete the request.',
        });
        await writes;
      }
    });
  });

  return app;
}
