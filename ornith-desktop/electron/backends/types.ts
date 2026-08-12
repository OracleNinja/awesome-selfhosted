import type { AppError } from '../../shared/types';
import type { WireMessage } from '../ollama/context';
import type { RawStats } from '../ollama/client';

/**
 * Provider-neutral chat backend.
 *
 * Deliberately the same handler shape the Ollama client already used, because
 * that contract described stream behaviour rather than Ollama. Local and online
 * modes are therefore interchangeable at one call site in the orchestrator.
 */

export interface BackendRequest {
  model: string;
  messages: WireMessage[];
  temperature: number;
  topP: number;
  numCtx: number;
  keepAlive: string;
  /** Only meaningful locally; the gateway decides its own reasoning handling. */
  think: boolean;
  /** Online only: allow the gateway to perform web retrieval for this turn. */
  web: boolean;
}

export interface BackendStatusEvent {
  /** Progress the backend wants surfaced, e.g. 'searching'. */
  status: 'searching' | 'reading' | 'generating';
}

export interface BackendSource {
  title: string;
  url: string;
  domain: string;
  cached: boolean;
}

export interface StreamHandlers {
  onOpen?: () => void;
  onDelta: (delta: { content: string; thinking: string }) => void;
  onDone: (stats: RawStats) => void;
  onError: (error: AppError) => void;
  /** Optional: backends that report progress phases. */
  onStatus?: (event: BackendStatusEvent) => void;
  /** Optional: backends that consulted sources. */
  onSources?: (sources: BackendSource[]) => void;
}

export interface ChatBackend {
  readonly name: 'local' | 'online';
  stream(request: BackendRequest, handlers: StreamHandlers): () => void;
}
