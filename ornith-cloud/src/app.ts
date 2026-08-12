import { Hono } from 'hono';
import type { Config } from './config.js';
import { log } from './log.js';
import { requireAuth, rateLimit } from './middleware/auth.js';
import { createChatRoute, type ChatDeps } from './routes/chat.js';
import type { AIProvider } from './ai/types.js';
import type { Retriever } from './retrieval/pipeline.js';

export interface AppDeps {
  config: Config;
  provider: AIProvider;
  retriever: Retriever;
  now?: () => number;
}

/** Builds the Hono app. Separated from the server so tests can drive it directly. */
export function createApp(deps: AppDeps): Hono {
  const app = new Hono();

  // Health is unauthenticated by design, and deliberately reveals nothing
  // about configuration beyond liveness.
  app.get('/health', (c) =>
    c.json({ status: 'ok', service: 'ornith-cloud', version: '0.1.0' }),
  );

  const v1 = new Hono();
  v1.use('*', rateLimit(60, 60_000));
  v1.use('*', requireAuth(deps.config.gatewayToken));
  v1.route('/', createChatRoute(deps as ChatDeps));

  app.route('/v1', v1);

  app.notFound((c) => c.json({ code: 'NOT_FOUND', message: 'Unknown endpoint.' }, 404));

  app.onError((err, c) => {
    // Structured, non-leaking: the detail is logged, never returned.
    log.error('gateway.unhandled', {
      detail: err instanceof Error ? err.message.slice(0, 200) : String(err),
    });
    return c.json({ code: 'INTERNAL', message: 'Unexpected server error.' }, 500);
  });

  return app;
}
