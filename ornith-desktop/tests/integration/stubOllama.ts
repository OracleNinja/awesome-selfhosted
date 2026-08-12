import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

/**
 * A real HTTP server speaking Ollama's wire protocol. Using this instead of
 * mocking fetch means the NDJSON framing, chunk boundaries, and connection
 * teardown are all exercised for real.
 */

export interface StubChunk {
  content?: string;
  thinking?: string;
  done?: boolean;
  eval_count?: number;
  eval_duration?: number;
  prompt_eval_count?: number;
  total_duration?: number;
  error?: string;
  /** Emit this exact string instead of a JSON object (for malformed-line tests). */
  raw?: string;
}

export interface StubOptions {
  version?: string;
  models?: string[];
  /** Chunks emitted by /api/chat, in order. */
  script?: StubChunk[];
  /** Delay between chunks, ms. */
  chunkDelayMs?: number;
  /**
   * Delay before the *first* chunk, with the response already open. This is
   * what a cold model load looks like on the wire.
   */
  initialDelayMs?: number;
  /** Force this HTTP status on /api/chat. */
  chatStatus?: number;
  /** Destroy the socket after this many chunks, simulating a dropped connection. */
  closeAfterChunks?: number;
  /** When false, /api/chat rejects a body containing `think`. */
  supportsThink?: boolean;
  /** Error body used for that rejection; real Ollama wording varies by cause. */
  thinkErrorMessage?: string;
  /** Never respond, to exercise the timeout path. */
  hang?: boolean;
  /** Bind a specific port, so a restarted stub can reclaim the same URL. */
  port?: number;
}

export interface StubHandle {
  url: string;
  close: () => Promise<void>;
  /** Bodies received on /api/chat, for asserting what the client sent. */
  chatRequests: Array<Record<string, unknown>>;
  setOptions: (patch: Partial<StubOptions>) => void;
}

const DEFAULT_SCRIPT: StubChunk[] = [
  { content: 'Hello' },
  { content: ' world' },
  { done: true, eval_count: 2, eval_duration: 1_000_000_000, total_duration: 1_200_000_000 },
];

export async function startStubOllama(initial: StubOptions = {}): Promise<StubHandle> {
  let options: StubOptions = {
    version: '0.5.0',
    models: ['ornith-en:latest'],
    script: DEFAULT_SCRIPT,
    chunkDelayMs: 0,
    supportsThink: true,
    ...initial,
  };

  const chatRequests: Array<Record<string, unknown>> = [];

  const server: Server = createServer((req, res) => {
    const url = req.url ?? '';

    if (options.hang) return; // never respond, never close

    if (req.method === 'GET' && url.startsWith('/api/version')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ version: options.version }));
      return;
    }

    if (req.method === 'GET' && url.startsWith('/api/tags')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ models: (options.models ?? []).map((name) => ({ name })) }));
      return;
    }

    if (req.method === 'POST' && url.startsWith('/api/chat')) {
      let body = '';
      req.on('data', (c) => {
        body += c;
      });

      req.on('end', () => {
        let parsed: Record<string, unknown> = {};
        try {
          parsed = JSON.parse(body) as Record<string, unknown>;
        } catch {
          /* leave empty */
        }
        chatRequests.push(parsed);

        if (options.supportsThink === false && 'think' in parsed) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(
            JSON.stringify({ error: options.thinkErrorMessage ?? 'unknown field "think"' }),
          );
          return;
        }

        if (options.chatStatus && options.chatStatus !== 200) {
          res.writeHead(options.chatStatus, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'stub failure' }));
          return;
        }

        // Non-streaming probe request.
        if (parsed.stream === false) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          const message: Record<string, unknown> = { role: 'assistant', content: 'hi' };
          if (options.supportsThink !== false && 'think' in parsed) message.thinking = '';
          res.end(JSON.stringify({ message, done: true }));
          return;
        }

        res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });

        const script = options.script ?? DEFAULT_SCRIPT;
        let i = 0;

        const emit = () => {
          if (i >= script.length) {
            res.end();
            return;
          }
          if (options.closeAfterChunks !== undefined && i >= options.closeAfterChunks) {
            // Abrupt teardown, as if Ollama died mid-answer.
            res.destroy();
            return;
          }

          const chunk = script[i];
          i += 1;

          if (chunk.raw !== undefined) {
            res.write(`${chunk.raw}\n`);
          } else {
            const payload: Record<string, unknown> = {
              model: 'ornith-en',
              created_at: new Date().toISOString(),
              message: {
                role: 'assistant',
                content: chunk.content ?? '',
                ...(chunk.thinking !== undefined ? { thinking: chunk.thinking } : {}),
              },
              done: chunk.done ?? false,
            };
            if (chunk.error) payload.error = chunk.error;
            if (chunk.done) {
              payload.eval_count = chunk.eval_count ?? 0;
              payload.eval_duration = chunk.eval_duration ?? 0;
              payload.prompt_eval_count = chunk.prompt_eval_count ?? 0;
              payload.total_duration = chunk.total_duration ?? 0;
            }
            res.write(`${JSON.stringify(payload)}\n`);
          }

          if (options.chunkDelayMs && options.chunkDelayMs > 0) {
            setTimeout(emit, options.chunkDelayMs);
          } else {
            setImmediate(emit);
          }
        };

        if (options.initialDelayMs && options.initialDelayMs > 0) {
          setTimeout(emit, options.initialDelayMs);
        } else {
          emit();
        }
      });
      return;
    }

    res.writeHead(404);
    res.end();
  });

  await new Promise<void>((resolve) => server.listen(options.port ?? 0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${port}`,
    chatRequests,
    setOptions: (patch) => {
      options = { ...options, ...patch };
    },
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}
