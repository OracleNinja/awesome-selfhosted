/**
 * Owns the lifecycle of a single generation: persistence, reasoning routing,
 * delta batching, and the IPC events the renderer reacts to.
 */
import type { WebContents } from 'electron';
import {
  COALESCE_INTERVAL_MS,
  COALESCE_MAX_BYTES,
  MODEL_LOADING_THRESHOLD_MS,
  STREAM_PERSIST_INTERVAL_MS,
} from '../../shared/defaults';
import { deriveTitle } from '../../shared/model';
import type { AppError, GenerationStats, Settings, ThinkingMode } from '../../shared/types';
import type { IpcEventMap } from '../../shared/ipc';
import type { ConversationStore } from '../store/conversations';
import { buildContext } from '../ollama/context';
import type { RawStats } from '../ollama/client';
import type { ChatBackend } from '../backends/types';
import { createCoalescer } from '../ollama/coalescer';
import { createThinkingParser } from '../ollama/thinking';
import { log } from '../log';

export interface OrchestratorDeps {
  store: ConversationStore;
  getSettings: () => Settings;
  getThinkingMode: () => ThinkingMode;
  /** Resolves the backend for this turn: local Ollama or the online gateway. */
  getBackend: () => ChatBackend;
}

interface ActiveStream {
  requestId: string;
  conversationId: string;
  messageId: string;
  abort: () => void;
  dispose: () => void;
}

export interface Orchestrator {
  start(
    req: { conversationId: string; requestId: string; userText: string },
    sender: WebContents,
  ): void;
  abort(requestId: string): void;
  abortAll(): void;
  readonly activeCount: number;
}

export function createOrchestrator(deps: OrchestratorDeps): Orchestrator {
  const active = new Map<string, ActiveStream>();

  function emit<K extends keyof IpcEventMap>(
    sender: WebContents,
    channel: K,
    payload: IpcEventMap[K],
  ): void {
    if (!sender.isDestroyed()) sender.send(channel, payload);
  }

  function start(
    req: { conversationId: string; requestId: string; userText: string },
    sender: WebContents,
  ): void {
    const { conversationId, requestId, userText } = req;
    const settings = deps.getSettings();

    if (!deps.store.exists(conversationId)) {
      emit(sender, 'chat:end', {
        requestId,
        outcome: {
          kind: 'error',
          error: { code: 'UNKNOWN', message: 'That conversation no longer exists.' },
        },
      });
      return;
    }

    const conversationBefore = deps.store.get(conversationId)!;
    const isFirstTurn = conversationBefore.messages.length === 0;

    const { userMessage, assistantMessage } = deps.store.beginTurn(
      conversationId,
      userText,
      settings.model,
    );

    // Titles are derived, not generated: a second model call would queue behind
    // this one, since Ollama serialises requests per model.
    const title = isFirstTurn ? deriveTitle(userText) : conversationBefore.title;
    if (isFirstTurn) deps.store.rename(conversationId, title);

    emit(sender, 'chat:started', {
      requestId,
      conversationId,
      userMessage,
      assistantMessage,
      title,
    });
    emit(sender, 'chat:state', { requestId, state: 'queued' });

    const history = [...conversationBefore.messages, userMessage];
    const { messages, dropped } = buildContext(history, settings.numCtx);
    if (dropped > 0) {
      emit(sender, 'chat:trimmed', { requestId, droppedMessages: dropped });
      log.warn('context.trimmed', { requestId, dropped });
    }

    const thinkingMode = deps.getThinkingMode();
    const inlineParser = thinkingMode === 'inline' ? createThinkingParser() : null;

    let content = '';
    let thinking = '';
    let finished = false;
    let sawFirstToken = false;

    const coalescer = createCoalescer({
      intervalMs: COALESCE_INTERVAL_MS,
      maxBytes: COALESCE_MAX_BYTES,
      onFlush: (batch) => emit(sender, 'chat:delta', { requestId, ...batch }),
    });

    const persistTimer = setInterval(() => {
      if (finished) return;
      try {
        deps.store.updateStreaming(assistantMessage.id, content, thinking);
      } catch (err) {
        log.error('persist.failed', { requestId, detail: String(err) });
      }
    }, STREAM_PERSIST_INTERVAL_MS);

    const loadingTimer = setTimeout(() => {
      if (!sawFirstToken && !finished) {
        emit(sender, 'chat:state', { requestId, state: 'loading-model' });
      }
    }, MODEL_LOADING_THRESHOLD_MS);

    function cleanup(): void {
      clearInterval(persistTimer);
      clearTimeout(loadingTimer);
      coalescer.dispose();
      active.delete(requestId);
    }

    function finish(
      status: 'complete' | 'cancelled' | 'error',
      extras: { error?: AppError; stats?: GenerationStats } = {},
    ): void {
      if (finished) return;
      finished = true;

      coalescer.flush();
      clearInterval(persistTimer);
      clearTimeout(loadingTimer);

      let thinkingIncomplete = false;
      if (inlineParser) {
        const tail = inlineParser.flush();
        content += tail.content;
        thinking += tail.thinking;
        if (tail.malformed) {
          // The stream ended inside an unclosed <think>. Keep the text and
          // label it, rather than discarding the model's output (SPEC §7.3).
          thinkingIncomplete = true;
          log.warn('thinking.unclosed', { requestId });
        }
      }

      emit(sender, 'chat:state', { requestId, state: 'finalising' });

      // An unclosed reasoning block is a presentation flag, not a failed turn:
      // the answer still stands, so the status stays whatever it was.
      const incompleteError = thinkingIncomplete
        ? ({
            code: 'STREAM_MALFORMED',
            message: 'The model’s reasoning block was never closed.',
          } as const)
        : undefined;

      try {
        deps.store.finalise(assistantMessage.id, {
          content,
          thinking,
          status,
          error: extras.error ?? incompleteError,
          stats: extras.stats,
        });
      } catch (err) {
        log.error('finalise.failed', { requestId, detail: String(err) });
      }

      const outcome =
        status === 'complete'
          ? ({ kind: 'complete', stats: extras.stats! } as const)
          : status === 'cancelled'
            ? ({ kind: 'cancelled', partial: true } as const)
            : ({
                kind: 'error',
                error: extras.error ?? { code: 'UNKNOWN', message: 'Something went wrong.' },
              } as const);

      emit(sender, 'chat:end', { requestId, outcome, thinkingIncomplete });
      cleanup();
      log.info('chat.finished', { requestId, status, chars: content.length });
    }

    const backend = deps.getBackend();

    const abort = backend.stream(
      {
        model: settings.model,
        messages,
        temperature: settings.temperature,
        topP: settings.topP,
        numCtx: settings.numCtx,
        keepAlive: settings.keepAlive,
        think: thinkingMode === 'structured',
        web: settings.webRetrieval,
      },
      {
        onStatus: ({ status }) => {
          // Online progress phases ride the existing chat:state channel rather
          // than a second status mechanism.
          if (finished) return;
          // 'generating' is the gateway's name for what the UI already calls
          // 'streaming'; searching/reading are genuinely new phases.
          const state = status === 'generating' ? 'streaming' : status;
          emit(sender, 'chat:state', { requestId, state });
        },

        onSources: (sources) => {
          if (!finished) emit(sender, 'chat:sources', { requestId, sources });
        },

        onDelta: ({ content: c, thinking: t }) => {
          if (!sawFirstToken) {
            sawFirstToken = true;
            clearTimeout(loadingTimer);
            emit(sender, 'chat:state', { requestId, state: 'streaming' });
          }

          // Structured mode arrives pre-separated; inline mode needs the parser.
          if (inlineParser) {
            const split = inlineParser.push(c);
            content += split.content;
            thinking += split.thinking + t;
            coalescer.push({ content: split.content, thinking: split.thinking + t });
          } else {
            content += c;
            thinking += t;
            coalescer.push({ content: c, thinking: t });
          }
        },

        onDone: (raw: RawStats) => {
          const seconds = raw.evalDurationNs / 1e9;
          finish('complete', {
            stats: {
              evalCount: raw.evalCount,
              evalDurationNs: raw.evalDurationNs,
              promptEvalCount: raw.promptEvalCount,
              totalDurationNs: raw.totalDurationNs,
              tokensPerSecond: seconds > 0 ? raw.evalCount / seconds : 0,
            },
          });
        },

        onError: (error) => {
          log.warn('chat.error', { requestId, code: error.code, detail: error.detail });
          finish('error', { error });
        },
      },
    );

    active.set(requestId, {
      requestId,
      conversationId,
      messageId: assistantMessage.id,
      abort,
      dispose: () => finish('cancelled'),
    });

    log.info('chat.started', {
      requestId,
      backend: backend.name,
      model: settings.model,
      contextMessages: messages.length,
    });
  }

  return {
    start,

    abort(requestId: string): void {
      const stream = active.get(requestId);
      if (!stream) return;
      stream.abort();
      stream.dispose();
    },

    abortAll(): void {
      for (const stream of [...active.values()]) {
        stream.abort();
        stream.dispose();
      }
      active.clear();
    },

    get activeCount() {
      return active.size;
    },
  };
}
