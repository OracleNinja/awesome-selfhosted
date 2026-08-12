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

export type StreamState =
  | 'queued'
  | 'loading-model'
  /** Online mode only: the gateway is running a web search. */
  | 'searching'
  /** Online mode only: the gateway is extracting fetched pages. */
  | 'reading'
  | 'streaming'
  | 'finalising';

export type AiMode = 'local' | 'online';

export interface AnsweredSource {
  title: string;
  url: string;
  domain: string;
  cached: boolean;
}

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

  /* ---- online mode ---- */

  /** Which backend answers. Local keeps everything on this Mac. */
  mode: AiMode;
  /** True once the user has made a first-run choice, so it is asked only once. */
  modeChosen: boolean;
  /** Base URL of the Ornith Cloud gateway. Not secret. */
  gatewayUrl: string;
  /**
   * Bearer token for the gateway. SECRET — main process only. It is
   * deliberately absent from PublicSettings and must never cross IPC.
   */
  gatewayToken: string;
  /** Allow the gateway to perform web retrieval when a question needs it. */
  webRetrieval: boolean;
}

/**
 * What the renderer is allowed to see. The token is replaced by a boolean so
 * the secret never enters renderer memory, where a rendering bug or a
 * compromised dependency could reach it.
 */
export type PublicSettings = Omit<Settings, 'gatewayToken'> & {
  gatewayTokenConfigured: boolean;
};

export function toPublicSettings(settings: Settings): PublicSettings {
  const { gatewayToken, ...rest } = settings;
  return { ...rest, gatewayTokenConfigured: gatewayToken.length > 0 };
}

export interface AppInfo {
  version: string;
  platform: NodeJS.Platform;
  ipcVersion: number;
}
