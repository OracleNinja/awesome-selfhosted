/**
 * Ollama HTTP client. Lives in the main process because Ollama validates the
 * Origin header (a renderer would need OLLAMA_ORIGINS widened) and because
 * keeping fetch out of the renderer lets the CSP be connect-src 'none'.
 */
import { PROBE_TIMEOUT_MS } from '../../shared/defaults';
import { isModelInstalled, resolveActiveModel } from '../../shared/model';
import type { AppError, OllamaStatus, ThinkingMode } from '../../shared/types';
import { createNdjsonParser, type OllamaChatChunk } from './ndjson';
import type { WireMessage } from './context';

export interface ChatRequest {
  host: string;
  model: string;
  messages: WireMessage[];
  temperature: number;
  topP: number;
  numCtx: number;
  keepAlive: string;
  /** Only sent when the model reports structured reasoning support. */
  think: boolean;
}

export interface RawStats {
  evalCount: number;
  evalDurationNs: number;
  promptEvalCount: number;
  totalDurationNs: number;
}

export interface StreamHandlers {
  /** Already-separated by field; inline parsing happens above this layer. */
  onDelta: (delta: { content: string; thinking: string }) => void;
  onDone: (stats: RawStats) => void;
  onError: (error: AppError) => void;
  /** Fired once the request is accepted, before any token. */
  onOpen?: () => void;
}

function timeoutSignal(ms: number): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return { signal: controller.signal, cancel: () => clearTimeout(timer) };
}

export function connectionError(host: string, err: unknown): AppError {
  const message = err instanceof Error ? err.message : String(err);

  if (/abort/i.test(message)) {
    return {
      code: 'OLLAMA_TIMEOUT',
      message: `Ollama at ${host} didn't respond in time.`,
      action: { label: 'Retry', kind: 'retry' },
      detail: message,
    };
  }
  return {
    code: 'OLLAMA_UNREACHABLE',
    message: `Can't reach Ollama at ${host}. Is it running? Start it with \`ollama serve\`.`,
    action: { label: 'Retry', kind: 'retry' },
    detail: message,
  };
}

export async function fetchStatus(
  host: string,
  configuredModel: string,
  defaultModel: string,
  thinkingMode: ThinkingMode,
): Promise<OllamaStatus> {
  const base: OllamaStatus = {
    connected: false,
    host,
    models: [],
    activeModel: configuredModel,
    activeModelInstalled: false,
    thinkingMode,
  };

  const { signal, cancel } = timeoutSignal(PROBE_TIMEOUT_MS);
  try {
    const [versionRes, tagsRes] = await Promise.all([
      fetch(`${host}/api/version`, { signal }),
      fetch(`${host}/api/tags`, { signal }),
    ]);

    const version = versionRes.ok
      ? ((await versionRes.json()) as { version?: string }).version
      : undefined;

    let models: string[] = [];
    if (tagsRes.ok) {
      const body = (await tagsRes.json()) as { models?: Array<{ name?: string }> };
      models = (body.models ?? [])
        .map((m) => m.name)
        .filter((n): n is string => typeof n === 'string')
        .sort((a, b) => a.localeCompare(b));
    }

    const activeModel = resolveActiveModel(models, configuredModel, defaultModel);

    return {
      ...base,
      connected: true,
      version,
      models,
      activeModel,
      // SPEC C4: must go through modelMatches, not Array.includes.
      activeModelInstalled: isModelInstalled(models, activeModel),
    };
  } catch (err) {
    return { ...base, error: connectionError(host, err) };
  } finally {
    cancel();
  }
}

/**
 * Asks for one token with `think: true` to learn how this model exposes
 * reasoning. Never throws — an unusable probe just means 'inline'.
 */
export async function probeThinkingMode(host: string, model: string): Promise<ThinkingMode> {
  const { signal, cancel } = timeoutSignal(PROBE_TIMEOUT_MS * 2);
  try {
    const res = await fetch(`${host}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
      body: JSON.stringify({
        model,
        messages: [{ role: 'user', content: 'hi' }],
        stream: false,
        think: true,
        options: { num_predict: 1 },
      }),
    });

    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      // Ollama is explicit when a model has no reasoning capability at all:
      //   {"error":"\"ornith-en:latest\" does not support thinking"}
      // Treating that as 'inline' would leave the <think> parser running over
      // output that can never contain those tags, so a literal `<think>` in an
      // answer would be swallowed into a hidden panel.
      if (/does not support thinking/i.test(detail)) return 'none';
      return 'inline';
    }

    const body = (await res.json()) as { message?: { thinking?: string } };
    // The field being present at all — even empty — means the server understood it.
    return body.message && 'thinking' in body.message ? 'structured' : 'inline';
  } catch {
    return 'inline';
  } finally {
    cancel();
  }
}

function httpError(status: number, body: string, model: string): AppError {
  const detail = body.trim().slice(0, 500);

  if (status === 404) {
    return {
      code: 'MODEL_NOT_FOUND',
      message: `The model \`${model}\` isn't installed in Ollama.`,
      action: { label: 'Open Settings', kind: 'open-settings' },
      detail,
    };
  }
  if (status >= 500) {
    return {
      code: 'MODEL_LOAD_FAILED',
      message: `Ollama couldn't load \`${model}\`. It may need more memory than is currently free.`,
      action: { label: 'Retry', kind: 'retry' },
      detail,
    };
  }
  return {
    code: 'UNKNOWN',
    message: `Ollama rejected the request (HTTP ${status}).`,
    action: { label: 'Retry', kind: 'retry' },
    detail,
  };
}

/** Starts a streaming chat. Returns an abort function. */
export function streamChat(request: ChatRequest, handlers: StreamHandlers): () => void {
  const controller = new AbortController();

  void (async () => {
    const body: Record<string, unknown> = {
      model: request.model,
      messages: request.messages,
      stream: true,
      keep_alive: request.keepAlive,
      options: {
        temperature: request.temperature,
        top_p: request.topP,
        // SPEC C5: never leave num_ctx implicit.
        num_ctx: request.numCtx,
      },
    };
    if (request.think) body.think = true;

    try {
      const res = await fetch(`${request.host}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify(body),
      });

      handlers.onOpen?.();

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        handlers.onError(httpError(res.status, text, request.model));
        return;
      }
      if (!res.body) {
        handlers.onError({
          code: 'EMPTY_RESPONSE',
          message: 'Ollama returned an empty response stream.',
          action: { label: 'Retry', kind: 'retry' },
        });
        return;
      }

      const parser = createNdjsonParser();
      const reader = res.body.getReader();
      let stats: RawStats = {
        evalCount: 0,
        evalDurationNs: 0,
        promptEvalCount: 0,
        totalDurationNs: 0,
      };
      let sawContent = false;

      const handleChunks = (chunks: OllamaChatChunk[]): AppError | null => {
        for (const chunk of chunks) {
          if (chunk.error) {
            return {
              code: 'UNKNOWN',
              message: 'Ollama reported an error while generating.',
              detail: chunk.error,
              action: { label: 'Retry', kind: 'retry' },
            };
          }

          const content = chunk.message?.content ?? '';
          const thinking = chunk.message?.thinking ?? '';
          if (content || thinking) {
            sawContent = sawContent || Boolean(content);
            handlers.onDelta({ content, thinking });
          }

          if (chunk.done) {
            stats = {
              evalCount: chunk.eval_count ?? 0,
              evalDurationNs: chunk.eval_duration ?? 0,
              promptEvalCount: chunk.prompt_eval_count ?? 0,
              totalDurationNs: chunk.total_duration ?? 0,
            };
          }
        }
        return null;
      };

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const err = handleChunks(parser.push(value));
        if (err) {
          handlers.onError(err);
          return;
        }
      }

      const tailErr = handleChunks(parser.flush());
      if (tailErr) {
        handlers.onError(tailErr);
        return;
      }

      if (!sawContent && stats.evalCount === 0) {
        handlers.onError({
          code: 'EMPTY_RESPONSE',
          message: 'Ornith returned an empty response.',
          action: { label: 'Retry', kind: 'retry' },
        });
        return;
      }

      handlers.onDone(stats);
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // Caller-initiated stop; the orchestrator handles the cancelled outcome.
        return;
      }
      handlers.onError({
        code: 'STREAM_INTERRUPTED',
        message: 'The connection to Ollama dropped. Your partial response has been saved.',
        action: { label: 'Retry', kind: 'retry' },
        detail: err instanceof Error ? err.message : String(err),
      });
    }
  })();

  return () => controller.abort();
}
