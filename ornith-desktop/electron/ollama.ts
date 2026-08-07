/**
 * Ollama HTTP client. Lives in the main process on purpose: Ollama rejects
 * cross-origin browser requests unless OLLAMA_ORIGINS is widened, and doing the
 * networking here means the renderer never needs network privileges at all.
 */

const OLLAMA_HOST = process.env.OLLAMA_HOST?.replace(/\/$/, '') || 'http://localhost:11434';

export interface WireMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface ChatOptions {
  model: string;
  messages: WireMessage[];
  temperature?: number;
  topP?: number;
}

export interface ChatStats {
  /** Tokens generated in the response. */
  evalCount: number;
  /** Nanoseconds spent generating (excludes prompt processing). */
  evalDurationNs: number;
  /** Nanoseconds for the whole request. */
  totalDurationNs: number;
}

export interface StatusResult {
  connected: boolean;
  host: string;
  version?: string;
  models: string[];
  error?: string;
}

/** A short timeout: these are localhost calls, so slow means broken. */
const PROBE_TIMEOUT_MS = 4000;

async function probe(path: string): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    return await fetch(`${OLLAMA_HOST}${path}`, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function describeConnectionError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  if (/abort/i.test(message)) {
    return `Ollama at ${OLLAMA_HOST} did not respond in time.`;
  }
  if (/ECONNREFUSED|fetch failed/i.test(message)) {
    return `Could not reach Ollama at ${OLLAMA_HOST}. Is it running? Try \`ollama serve\`.`;
  }
  return message;
}

export async function getStatus(): Promise<StatusResult> {
  try {
    const [versionRes, tagsRes] = await Promise.all([probe('/api/version'), probe('/api/tags')]);

    const version = versionRes.ok
      ? ((await versionRes.json()) as { version?: string }).version
      : undefined;

    let models: string[] = [];
    if (tagsRes.ok) {
      const body = (await tagsRes.json()) as { models?: Array<{ name?: string }> };
      models = (body.models ?? [])
        .map((m) => m.name)
        .filter((name): name is string => typeof name === 'string')
        .sort((a, b) => a.localeCompare(b));
    }

    return { connected: true, host: OLLAMA_HOST, version, models };
  } catch (err) {
    return { connected: false, host: OLLAMA_HOST, models: [], error: describeConnectionError(err) };
  }
}

export interface StreamHandlers {
  onDelta: (text: string) => void;
  onDone: (stats: ChatStats) => void;
  onError: (message: string) => void;
}

/**
 * Streams a chat completion. Ollama's /api/chat with stream:true emits
 * newline-delimited JSON, one object per token-ish chunk, with a final object
 * carrying `done: true` and timing counters.
 *
 * Returns an abort function.
 */
export function streamChat(options: ChatOptions, handlers: StreamHandlers): () => void {
  const controller = new AbortController();

  void (async () => {
    try {
      const res = await fetch(`${OLLAMA_HOST}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          model: options.model,
          messages: options.messages,
          stream: true,
          options: {
            temperature: options.temperature ?? 0.6,
            top_p: options.topP ?? 0.95,
          },
        }),
      });

      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        handlers.onError(
          `Ollama returned ${res.status}. ${detail.trim() || 'Check that the model name exists.'}`,
        );
        return;
      }
      if (!res.body) {
        handlers.onError('Ollama returned an empty response stream.');
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let stats: ChatStats = { evalCount: 0, evalDurationNs: 0, totalDurationNs: 0 };

      // NDJSON: accumulate, split on newlines, keep the trailing partial line.
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;

          let payload: {
            message?: { content?: string };
            done?: boolean;
            error?: string;
            eval_count?: number;
            eval_duration?: number;
            total_duration?: number;
          };
          try {
            payload = JSON.parse(trimmed);
          } catch {
            continue; // Ignore malformed keepalive noise rather than killing the stream.
          }

          if (payload.error) {
            handlers.onError(payload.error);
            return;
          }
          const delta = payload.message?.content;
          if (delta) handlers.onDelta(delta);

          if (payload.done) {
            stats = {
              evalCount: payload.eval_count ?? 0,
              evalDurationNs: payload.eval_duration ?? 0,
              totalDurationNs: payload.total_duration ?? 0,
            };
          }
        }
      }

      handlers.onDone(stats);
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // User pressed Stop. Treat the partial text as a finished turn.
        handlers.onDone({ evalCount: 0, evalDurationNs: 0, totalDurationNs: 0 });
        return;
      }
      handlers.onError(describeConnectionError(err));
    }
  })();

  return () => controller.abort();
}
