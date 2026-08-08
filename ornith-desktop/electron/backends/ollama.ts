import { streamChat } from '../ollama/client';
import type { ChatBackend, BackendRequest, StreamHandlers } from './types';

/**
 * Local mode. A thin delegation to the existing Ollama client — the streaming
 * implementation is untouched, so local behaviour is bit-for-bit what it was
 * before online mode existed.
 */
export function createOllamaBackend(host: string): ChatBackend {
  return {
    name: 'local',

    stream(request: BackendRequest, handlers: StreamHandlers): () => void {
      return streamChat(
        {
          host,
          model: request.model,
          messages: request.messages,
          temperature: request.temperature,
          topP: request.topP,
          numCtx: request.numCtx,
          keepAlive: request.keepAlive,
          think: request.think,
        },
        {
          onOpen: handlers.onOpen,
          onDelta: handlers.onDelta,
          onDone: handlers.onDone,
          onError: handlers.onError,
        },
      );
    },
  };
}
