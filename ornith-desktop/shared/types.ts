/**
 * Types shared by main, preload, and renderer. Keep this file free of imports
 * from any of those layers so it stays trivially testable.
 */

export type Role = 'user' | 'assistant' | 'system';

export type MessageStatus = 'complete' | 'streaming' | 'cancelled' | 'error';

export type AppErrorCode =
  | 'OLLAMA_UNREACHABLE'
  | 'OLLAMA_TIMEOUT'
  | 'MODEL_NOT_FOUND'
  | 'MODEL_LOAD_FAILED'
  | 'STREAM_MALFORMED'
  | 'STREAM_INTERRUPTED'
  | 'EMPTY_RESPONSE'
  | 'CONTEXT_OVERFLOW'
  | 'STORAGE_CORRUPT'
  | 'STORAGE_WRITE_FAILED'
  | 'SETTINGS_INVALID'
  | 'UNKNOWN';

export interface AppErrorAction {
  label: string;
  kind: 'retry' | 'open-settings' | 'copy-details';
}

export interface AppError {
  code: AppErrorCode;
  /** Shown to the user: plain English, actionable, never raw JSON or a stack. */
  message: string;
  action?: AppErrorAction;
  /** Logged only, never rendered. */
  detail?: string;
}

export interface GenerationStats {
  evalCount: number;
  evalDurationNs: number;
  promptEvalCount: number;
  totalDurationNs: number;
  tokensPerSecond: number;
}

export interface ChatMessage {
  id: string;
  seq: number;
  role: Role;
  content: string;
  thinking: string;
  status: MessageStatus;
  error?: AppError;
  stats?: GenerationStats;
  createdAt: number;
}

export interface Conversation {
  id: string;
  title: string;
  model: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

export type ConversationSummary = Omit<Conversation, 'messages'> & {
  messageCount: number;
};

/** How the active model exposes reasoning, probed once per model per session. */
export type ThinkingMode = 'structured' | 'inline' | 'none';

export interface OllamaStatus {
  connected: boolean;
  host: string;
  version?: string;
  models: string[];
  activeModel: string;
  activeModelInstalled: boolean;
  thinkingMode: ThinkingMode;
  error?: AppError;
}

export type StreamState = 'queued' | 'loading-model' | 'streaming' | 'finalising';

export type StreamOutcome =
  | { kind: 'complete'; stats: GenerationStats }
  | { kind: 'cancelled'; partial: true }
  | { kind: 'error'; error: AppError };

export type ThemePreference = 'dark' | 'light' | 'system';

export interface Settings {
  schemaVersion: 1;
  ollamaUrl: string;
  model: string;
  temperature: number;
  topP: number;
  numCtx: number;
  keepAlive: string;
  theme: ThemePreference;
  language: 'en';
  showThinkingByDefault: boolean;
  sendOnEnter: boolean;

  /** Speak every assistant reply. A reply to a spoken prompt is always spoken. */
  speakResponses: boolean;
  /** macOS voice name; empty means the system default. */
  voiceName: string;
  /** Words per minute passed to `say`. */
  speechRate: number;
  /** BCP-47 locale for on-device speech recognition. */
  sttLocale: string;
}

export interface AppInfo {
  version: string;
  platform: NodeJS.Platform;
  ipcVersion: number;
}
