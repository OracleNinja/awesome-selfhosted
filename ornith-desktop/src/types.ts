export type Role = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  createdAt: number;
  /** Set when the turn failed, so the UI can show it distinctly from prose. */
  error?: string;
  /** Tokens per second for a completed assistant turn, when Ollama reported timings. */
  tokensPerSecond?: number;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

export interface ChatStats {
  evalCount: number;
  evalDurationNs: number;
  totalDurationNs: number;
}

export interface StatusResult {
  connected: boolean;
  host: string;
  version?: string;
  models: string[];
  error?: string;
}

export const DEFAULT_MODEL = 'ornith-en';

export function createId(): string {
  return crypto.randomUUID();
}
