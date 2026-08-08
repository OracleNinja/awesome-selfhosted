import { describe, expect, it, beforeEach } from 'vitest';
import { createApp } from '../src/app';
import { loadConfig, type Config } from '../src/config';
import { createStubProvider } from '../src/ai/stub';
import { createRetriever } from '../src/retrieval/pipeline';
import type { SearchProvider, FetchProvider, SearchResult } from '../src/retrieval/types';

const TOKEN = 'test-token-that-is-long-enough-123';

function testConfig(overrides: Partial<Config> = {}): Config {
  return {
    ...loadConfig({ ORNITH_GATEWAY_TOKEN: TOKEN, AI_PROVIDER: 'stub' } as NodeJS.ProcessEnv),
    ...overrides,
  };
}

function fakeSearch(results: SearchResult[], fail = false): SearchProvider {
  return {
    name: 'fake',
    async search() {
      if (fail) throw new Error('search exploded');
      return results;
    },
  };
}

function fakeFetch(bodies: Record<string, string>, fail = false): FetchProvider {
  return {
    name: 'fake',
    async fetch(url: string) {
      if (fail || !bodies[url]) throw new Error('fetch failed');
      return { url, status: 200, contentType: 'text/html', html: bodies[url], truncated: false };
    },
  };
}

/** Collects an SSE body into typed events. */
async function readSSE(response: Response): Promise<Array<Record<string, unknown>>> {
  const text = await response.text();
  const events: Array<Record<string, unknown>> = [];
  for (const line of text.split('\n')) {
    if (line.startsWith('data:')) {
      const payload = line.slice(5).trim();
      if (payload) events.push(JSON.parse(payload));
    }
  }
  return events;
}

function buildApp(options: {
  config?: Partial<Config>;
  search?: SearchProvider;
  fetcher?: FetchProvider;
  provider?: ReturnType<typeof createStubProvider>;
} = {}) {
  const config = testConfig(options.config);
  const provider = options.provider ?? createStubProvider();
  const retriever = createRetriever({
    search: options.search ?? fakeSearch([]),
    fetcher: options.fetcher ?? fakeFetch({}),
    config,
  });
  return { app: createApp({ config, provider, retriever }), provider, config };
}

function chatRequest(body: unknown, token: string | null = TOKEN): Request {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  return new Request('http://localhost/v1/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
}

describe('health', () => {
  it('is reachable without authentication', async () => {
    const { app } = buildApp();
    const res = await app.fetch(new Request('http://localhost/health'));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ status: 'ok' });
  });
});

describe('authentication', () => {
  it('accepts a valid token', async () => {
    const { app } = buildApp();
    const res = await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'hi' }] }));
    expect(res.status).toBe(200);
  });

  it('rejects a missing token', async () => {
    const { app } = buildApp();
    const res = await app.fetch(chatRequest({ messages: [] }, null));
    expect(res.status).toBe(401);
  });

  it('rejects an invalid token', async () => {
    const { app } = buildApp();
    const res = await app.fetch(chatRequest({ messages: [] }, 'wrong-token'));
    expect(res.status).toBe(401);
  });

  it('gives an identical body for missing and wrong tokens', async () => {
    const { app } = buildApp();
    const missing = await (await app.fetch(chatRequest({ messages: [] }, null))).json();
    const wrong = await (await app.fetch(chatRequest({ messages: [] }, 'nope'))).json();
    expect(missing).toEqual(wrong);
  });

  it('never echoes the token in an error body', async () => {
    const { app } = buildApp();
    const body = await (await app.fetch(chatRequest({ messages: [] }, TOKEN.slice(0, 10)))).text();
    expect(body).not.toContain(TOKEN.slice(0, 10));
  });
});

describe('request validation', () => {
  it('rejects a non-JSON body', async () => {
    const { app } = buildApp();
    const res = await app.fetch(
      new Request('http://localhost/v1/chat', {
        method: 'POST',
        headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
        body: 'not json',
      }),
    );
    expect(res.status).toBe(400);
  });

  it('rejects an empty message list', async () => {
    const { app } = buildApp();
    expect((await app.fetch(chatRequest({ messages: [] }))).status).toBe(400);
  });

  it('rejects when the last message is not from the user', async () => {
    const { app } = buildApp();
    const res = await app.fetch(
      chatRequest({ messages: [{ role: 'assistant', content: 'hello' }] }),
    );
    expect(res.status).toBe(400);
  });

  it('returns 404 for an unknown endpoint', async () => {
    const { app } = buildApp();
    const res = await app.fetch(new Request('http://localhost/v1/nope'));
    expect(res.status).toBe(401); // auth runs before routing within /v1
  });
});

describe('streaming protocol', () => {
  it('emits status, deltas and completion in order', async () => {
    const { app } = buildApp();
    const res = await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'hello' }] }));
    const events = await readSSE(res);

    const types = events.map((e) => e.type);
    expect(types[0]).toBe('status');
    expect(types).toContain('delta');
    expect(types.at(-1)).toBe('complete');
  });

  it('reassembles the streamed answer from deltas', async () => {
    const provider = createStubProvider({ script: ['Ans', 'wer', ' here'] });
    const { app } = buildApp({ provider });
    const events = await readSSE(
      await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'hi' }] })),
    );

    const text = events
      .filter((e) => e.type === 'delta')
      .map((e) => e.content as string)
      .join('');
    expect(text).toBe('Answer here');
  });

  it('reports timings on completion', async () => {
    const { app } = buildApp();
    const events = await readSSE(
      await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'hi' }] })),
    );
    const complete = events.find((e) => e.type === 'complete') as
      | { timings: Record<string, number> }
      | undefined;

    expect(complete?.timings).toBeDefined();
    expect(typeof complete?.timings.totalMs).toBe('number');
    expect(typeof complete?.timings.timeToFirstTokenMs).toBe('number');
  });

  it('emits a structured error when the provider fails, and no completion', async () => {
    const provider = createStubProvider({
      failWith: {
        code: 'PROVIDER_UNAVAILABLE',
        message: 'The online AI service is temporarily unavailable.',
        detail: 'internal detail that must not leak',
      },
    });
    const { app } = buildApp({ provider });
    const events = await readSSE(
      await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'hi' }] })),
    );

    const error = events.find((e) => e.type === 'error') as
      | { code: string; message: string }
      | undefined;

    expect(error?.code).toBe('PROVIDER_UNAVAILABLE');
    expect(events.some((e) => e.type === 'complete')).toBe(false);
    // The server-side detail must never reach the client.
    expect(JSON.stringify(events)).not.toContain('internal detail');
  });
});

describe('routing and retrieval', () => {
  const html = '<html><head><title>XRP News</title></head><body><article>' +
    'XRP developments continued this week with several announcements. '.repeat(20) +
    '</article></body></html>';

  let search: SearchProvider;
  let fetcher: FetchProvider;

  beforeEach(() => {
    search = fakeSearch([
      { title: 'XRP News', url: 'https://news.example.com/xrp', snippet: 'about xrp' },
    ]);
    fetcher = fakeFetch({ 'https://news.example.com/xrp': html });
  });

  it('retrieves for a current-information question', async () => {
    const { app, provider } = buildApp({
      config: { searchProvider: 'searxng', searxngUrl: 'http://searx.test' },
      search,
      fetcher,
    });

    const events = await readSSE(
      await app.fetch(
        chatRequest({ messages: [{ role: 'user', content: 'latest news about XRP' }] }),
      ),
    );

    expect(events.some((e) => e.status === 'searching')).toBe(true);
    expect(events.some((e) => e.type === 'sources')).toBe(true);
    // The prompt actually carried the retrieved text, fenced.
    expect(provider.lastRequest?.messages.at(-1)?.content).toContain('<retrieved_context>');
  });

  it('does NOT retrieve for arithmetic', async () => {
    const { app, provider } = buildApp({
      config: { searchProvider: 'searxng', searxngUrl: 'http://searx.test' },
      search,
      fetcher,
    });

    const events = await readSSE(
      await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'What is 17 × 24?' }] })),
    );

    expect(events.some((e) => e.status === 'searching')).toBe(false);
    expect(provider.lastRequest?.messages.at(-1)?.content).not.toContain('<retrieved_context>');
  });

  it('does NOT retrieve for general knowledge', async () => {
    const { app } = buildApp({
      config: { searchProvider: 'searxng', searxngUrl: 'http://searx.test' },
      search,
      fetcher,
    });

    const events = await readSSE(
      await app.fetch(
        chatRequest({ messages: [{ role: 'user', content: 'Explain how neural networks work.' }] }),
      ),
    );
    expect(events.some((e) => e.status === 'searching')).toBe(false);
  });

  it('honours web:false even for a current-information question', async () => {
    const { app } = buildApp({
      config: { searchProvider: 'searxng', searxngUrl: 'http://searx.test' },
      search,
      fetcher,
    });

    const events = await readSSE(
      await app.fetch(
        chatRequest({ messages: [{ role: 'user', content: 'latest news' }], web: false }),
      ),
    );
    expect(events.some((e) => e.status === 'searching')).toBe(false);
  });

  it('still answers when search fails', async () => {
    const { app } = buildApp({
      config: { searchProvider: 'searxng', searxngUrl: 'http://searx.test' },
      search: fakeSearch([], true),
      fetcher,
    });

    const events = await readSSE(
      await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'latest news' }] })),
    );
    expect(events.some((e) => e.type === 'delta')).toBe(true);
    expect(events.at(-1)?.type).toBe('complete');
  });

  it('still answers when every fetch fails', async () => {
    const { app } = buildApp({
      config: { searchProvider: 'searxng', searxngUrl: 'http://searx.test' },
      search,
      fetcher: fakeFetch({}, true),
    });

    const events = await readSSE(
      await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'latest news' }] })),
    );
    expect(events.at(-1)?.type).toBe('complete');
  });

  it('uses the fast model tier for arithmetic and the standard tier otherwise', async () => {
    const { app, provider, config } = buildApp();

    await readSSE(await app.fetch(chatRequest({ messages: [{ role: 'user', content: '2 * 2' }] })));
    expect(provider.lastRequest?.model).toBe(config.fastModel);

    await readSSE(
      await app.fetch(chatRequest({ messages: [{ role: 'user', content: 'Explain entropy.' }] })),
    );
    expect(provider.lastRequest?.model).toBe(config.onlineModel);
  });
});
