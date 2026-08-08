import { afterEach, describe, expect, it } from 'vitest';
import { fetchStatus, probeThinkingMode, streamChat } from '../../electron/ollama/client';
import type { AppError } from '../../shared/types';
import { startStubOllama, type StubHandle } from './stubOllama';

let stub: StubHandle | null = null;

afterEach(async () => {
  await stub?.close();
  stub = null;
});

function collect(host: string, overrides: Partial<Parameters<typeof streamChat>[0]> = {}) {
  return new Promise<{
    content: string;
    thinking: string;
    stats?: { evalCount: number };
    error?: AppError;
    abort: () => void;
  }>((resolve) => {
    let content = '';
    let thinking = '';

    const abort = streamChat(
      {
        host,
        model: 'ornith-en',
        messages: [{ role: 'user', content: 'hi' }],
        temperature: 0.6,
        topP: 0.95,
        numCtx: 8192,
        keepAlive: '15m',
        think: false,
        ...overrides,
      },
      {
        onDelta: (d) => {
          content += d.content;
          thinking += d.thinking;
        },
        onDone: (stats) => resolve({ content, thinking, stats, abort }),
        onError: (error) => resolve({ content, thinking, error, abort }),
      },
    );
  });
}

describe('ollama client against a stub server', () => {
  it('assembles a multi-chunk stream and reports stats', async () => {
    stub = await startStubOllama();
    const result = await collect(stub.url);

    expect(result.error).toBeUndefined();
    expect(result.content).toBe('Hello world');
    expect(result.stats?.evalCount).toBe(2);
  });

  it('sends num_ctx explicitly (SPEC C5)', async () => {
    stub = await startStubOllama();
    await collect(stub.url, { numCtx: 16384 });

    const sent = stub.chatRequests.at(-1)!;
    expect((sent.options as Record<string, unknown>).num_ctx).toBe(16384);
    expect(sent.keep_alive).toBe('15m');
  });

  it('omits the think field when the model does not support it', async () => {
    stub = await startStubOllama();
    await collect(stub.url, { think: false });
    expect(stub.chatRequests.at(-1)!).not.toHaveProperty('think');
  });

  it('separates structured thinking from content', async () => {
    stub = await startStubOllama({
      script: [
        { thinking: 'let me think' },
        { thinking: ' harder' },
        { content: 'The answer' },
        { done: true, eval_count: 3, eval_duration: 1_000_000_000 },
      ],
    });

    const result = await collect(stub.url, { think: true });
    expect(result.thinking).toBe('let me think harder');
    expect(result.content).toBe('The answer');
  });

  it('maps a 404 to MODEL_NOT_FOUND', async () => {
    stub = await startStubOllama({ chatStatus: 404 });
    const result = await collect(stub.url);
    expect(result.error?.code).toBe('MODEL_NOT_FOUND');
    expect(result.error?.message).toContain('ornith-en');
  });

  it('maps a 500 to MODEL_LOAD_FAILED', async () => {
    stub = await startStubOllama({ chatStatus: 500 });
    const result = await collect(stub.url);
    expect(result.error?.code).toBe('MODEL_LOAD_FAILED');
  });

  it('reports STREAM_INTERRUPTED and keeps partial text when the server dies', async () => {
    stub = await startStubOllama({
      script: [{ content: 'partial ' }, { content: 'more' }, { done: true }],
      closeAfterChunks: 1,
      chunkDelayMs: 5,
    });

    const result = await collect(stub.url);
    expect(result.error?.code).toBe('STREAM_INTERRUPTED');
    expect(result.content).toBe('partial '); // partial preserved
  });

  it('skips a malformed line without killing the stream', async () => {
    stub = await startStubOllama({
      script: [
        { content: 'a' },
        { raw: 'this is not json' },
        { content: 'b' },
        { done: true, eval_count: 2, eval_duration: 1_000_000_000 },
      ],
    });

    const result = await collect(stub.url);
    expect(result.error).toBeUndefined();
    expect(result.content).toBe('ab');
  });

  it('surfaces an error payload embedded in the stream', async () => {
    stub = await startStubOllama({ script: [{ error: 'out of memory' }] });
    const result = await collect(stub.url);
    expect(result.error?.detail).toBe('out of memory');
  });

  it('reports EMPTY_RESPONSE when the model produces nothing', async () => {
    stub = await startStubOllama({ script: [{ done: true, eval_count: 0 }] });
    const result = await collect(stub.url);
    expect(result.error?.code).toBe('EMPTY_RESPONSE');
  });

  it('abort stops the stream without reporting an error', async () => {
    stub = await startStubOllama({
      script: Array.from({ length: 50 }, (_, i) => ({ content: `${i} ` })),
      chunkDelayMs: 5,
    });

    let content = '';
    let errored = false;
    let done = false;

    const abort = streamChat(
      {
        host: stub.url,
        model: 'ornith-en',
        messages: [{ role: 'user', content: 'hi' }],
        temperature: 0.6,
        topP: 0.95,
        numCtx: 8192,
        keepAlive: '15m',
        think: false,
      },
      {
        onDelta: (d) => {
          content += d.content;
        },
        onDone: () => {
          done = true;
        },
        onError: () => {
          errored = true;
        },
      },
    );

    await new Promise((r) => setTimeout(r, 40));
    abort();
    await new Promise((r) => setTimeout(r, 40));

    expect(content.length).toBeGreaterThan(0);
    expect(content.length).toBeLessThan(200); // stopped early
    expect(errored).toBe(false);
    expect(done).toBe(false);
  });
});

describe('status detection', () => {
  it('detects connection, version, and models', async () => {
    stub = await startStubOllama({ models: ['ornith-en:latest', 'llama3.1:8b'] });
    const status = await fetchStatus(stub.url, 'ornith-en', 'ornith-en', 'none');

    expect(status.connected).toBe(true);
    expect(status.version).toBe('0.5.0');
    expect(status.models).toEqual(['llama3.1:8b', 'ornith-en:latest']);
  });

  // The bug a naive includes() would produce.
  it('detects ornith-en when Ollama reports ornith-en:latest (SPEC C4)', async () => {
    stub = await startStubOllama({ models: ['ornith-en:latest'] });
    const status = await fetchStatus(stub.url, 'ornith-en', 'ornith-en', 'none');

    expect(status.activeModel).toBe('ornith-en');
    expect(status.activeModelInstalled).toBe(true);
  });

  it('reports a missing model without claiming disconnection', async () => {
    stub = await startStubOllama({ models: ['llama3.1:8b'] });
    const status = await fetchStatus(stub.url, 'nonexistent', 'also-missing', 'none');

    expect(status.connected).toBe(true);
    expect(status.activeModelInstalled).toBe(true); // fell back to llama3.1:8b
    expect(status.activeModel).toBe('llama3.1:8b');
  });

  it('reports OLLAMA_UNREACHABLE when nothing is listening', async () => {
    const status = await fetchStatus('http://127.0.0.1:1', 'ornith-en', 'ornith-en', 'none');
    expect(status.connected).toBe(false);
    expect(status.error?.code).toBe('OLLAMA_UNREACHABLE');
    expect(status.error?.message).toContain('ollama serve');
  });

  it('recovers once the server starts responding again', async () => {
    stub = await startStubOllama();
    const url = stub.url;

    expect((await fetchStatus(url, 'ornith-en', 'ornith-en', 'none')).connected).toBe(true);
    await stub.close();
    stub = null;
    expect((await fetchStatus(url, 'ornith-en', 'ornith-en', 'none')).connected).toBe(false);
  });
});

describe('thinking capability probe', () => {
  it('reports structured when the server returns a thinking field', async () => {
    stub = await startStubOllama({ supportsThink: true });
    expect(await probeThinkingMode(stub.url, 'ornith-en')).toBe('structured');
  });

  it('falls back to inline when the server rejects the think field', async () => {
    stub = await startStubOllama({ supportsThink: false });
    expect(await probeThinkingMode(stub.url, 'ornith-en')).toBe('inline');
  });

  it('falls back to inline when the server is unreachable', async () => {
    expect(await probeThinkingMode('http://127.0.0.1:1', 'ornith-en')).toBe('inline');
  });
});
