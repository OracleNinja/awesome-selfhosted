import type { AppError } from '../../shared/types';
import { log } from '../log';
import { createSseParser } from './sse';
import type { BackendRequest, BackendSource, ChatBackend, StreamHandlers } from './types';

export interface GatewayOptions {
  url: string;
  /** Bearer token. Held in main only; never crosses IPC to the renderer. */
  token: string;
  timeoutMs?: number;
}

interface GatewayEvent {
  type?: string;
  status?: 'searching' | 'reading' | 'generating';
  content?: string;
  sources?: BackendSource[];
  code?: string;
  message?: string;
  timings?: Record<string, number>;
  usage?: Record<string, number>;
}

function gatewayError(code: string, message: string, detail?: string): AppError {
  return {
    code: 'UNKNOWN',
    message,
    detail: detail ? `${code}: ${detail}` : code,
    action: { label: 'Retry', kind: 'retry' },
  };
}

function describeHttp(status: number): AppError {
  if (status === 401 || status === 403) {
    return {
      code: 'UNKNOWN',
      message: 'The online service rejected this app’s credentials. Check the token in Settings.',
      action: { label: 'Open Settings', kind: 'open-settings' },
      detail: `HTTP ${status}`,
    };
  }
  if (status === 429) {
    return gatewayError('RATE_LIMITED', 'The online service is rate limiting requests. Try again shortly.');
  }
  if (status >= 500) {
    return gatewayError('UPSTREAM', 'The online service is temporarily unavailable.', `HTTP ${status}`);
  }
  return gatewayError('HTTP', `The online service rejected the request (HTTP ${status}).`);
}

/**
 * Online mode. Runs in the main process: the renderer's CSP is
 * connect-src 'none', so it could not make this call even if asked — which is
 * exactly why the bearer token never needs to leave main.
 */
export function createGatewayBackend(options: GatewayOptions): ChatBackend {
  const timeoutMs = options.timeoutMs ?? 60_000;
  const base = options.url.replace(/\/$/, '');

  return {
    name: 'online',

    stream(request: BackendRequest, handlers: StreamHandlers): () => void {
      const controller = new AbortController();
      // A hung gateway must not leave the UI streaming forever. The flag
      // distinguishes our timeout from the user pressing Stop — abort() always
      // populates signal.reason, so the reason alone cannot tell them apart.
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs);

      void (async () => {
        let sawContent = false;
        let reportedError = false;

        try {
          const res = await fetch(`${base}/v1/chat`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${options.token}`,
              Accept: 'text/event-stream',
            },
            signal: controller.signal,
            body: JSON.stringify({
              messages: request.messages.filter((m) => m.role !== 'system'),
              mode: 'online',
              web: request.web,
            }),
          });

          handlers.onOpen?.();

          if (!res.ok) {
            handlers.onError(describeHttp(res.status));
            return;
          }
          if (!res.body) {
            handlers.onError(gatewayError('EMPTY', 'The online service returned no response.'));
            return;
          }

          const parser = createSseParser();
          const reader = res.body.getReader();

          const handle = (frames: ReturnType<typeof parser.push>): boolean => {
            for (const frame of frames) {
              let payload: GatewayEvent;
              try {
                payload = JSON.parse(frame.data) as GatewayEvent;
              } catch {
                continue; // malformed frame: skip, do not kill the stream
              }

              switch (payload.type) {
                case 'status':
                  if (payload.status) handlers.onStatus?.({ status: payload.status });
                  break;
                case 'sources':
                  if (Array.isArray(payload.sources)) handlers.onSources?.(payload.sources);
                  break;
                case 'delta':
                  if (typeof payload.content === 'string' && payload.content) {
                    sawContent = true;
                    handlers.onDelta({ content: payload.content, thinking: '' });
                  }
                  break;
                case 'error':
                  reportedError = true;
                  handlers.onError(
                    gatewayError(
                      payload.code ?? 'UNKNOWN',
                      payload.message ?? 'The online service reported an error.',
                    ),
                  );
                  return true; // stop
                case 'complete':
                  log.info('gateway.timings', payload.timings ?? {});
                  break;
                default:
                  break;
              }
            }
            return false;
          };

          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            if (handle(parser.push(value))) return;
          }
          if (handle(parser.flush())) return;

          if (reportedError) return;

          if (!sawContent) {
            handlers.onError(gatewayError('EMPTY', 'The online service returned an empty answer.'));
            return;
          }

          // The gateway reports token usage but not Ollama-style timings; the
          // UI's tok/s readout is simply absent for online turns.
          handlers.onDone({
            evalCount: 0,
            evalDurationNs: 0,
            promptEvalCount: 0,
            totalDurationNs: 0,
          });
        } catch (err) {
          if (err instanceof Error && err.name === 'AbortError') {
            if (!timedOut) return; // user pressed Stop; not an error
            handlers.onError(
              gatewayError('TIMEOUT', 'The online service took too long to respond.'),
            );
            return;
          }
          handlers.onError({
            code: 'OLLAMA_UNREACHABLE',
            message:
              'Online service unavailable. Check your connection, or switch to Local Mode in Settings.',
            action: { label: 'Open Settings', kind: 'open-settings' },
            detail: err instanceof Error ? err.message.slice(0, 200) : String(err),
          });
        } finally {
          clearTimeout(timer);
        }
      })();

      return () => controller.abort();
    },
  };
}
