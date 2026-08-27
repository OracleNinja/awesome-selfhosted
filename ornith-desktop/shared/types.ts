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

/**
 * Conversation export.
 *
 * Markdown is for reading and sharing; JSON is for feeding a conversation into
 * something else, so it stays close to the stored shape rather than being
 * prettified. Reasoning is excluded by default: it is the model's scratch work,
 * it is often long, and a transcript someone intends to share is rarely
 * improved by it.
 */
/**
 * Conversation search.
 *
 * Backed by SQLite FTS5, which the shipping runtime provides (Electron 39.8.10,
 * SQLite 3.51.2 — verified by spike, not assumed). The index covers message
 * `content` only. Reasoning text is the model's scratch work and searching it
 * surfaces hits a user cannot see in the transcript; titles are matched in a
 * later version, so a hit always carries its conversation's title but a query
 * matching only a title finds nothing today.
 *
 * The query is never interpreted as FTS5 syntax. Passing a raw user string to
 * MATCH raises a SQLite error on completely ordinary typing -- a lone quote,
 * a trailing "AND", "a-b", "a:b" all throw -- so the store quotes the whole
 * query as a single FTS5 string literal and appends a prefix wildcard. The
 * consequence is deliberate: FTS5 boolean operators are not exposed, and a
 * typed "AND" is searched for literally.
 */
export interface SearchRequest {
  /** Raw text as typed. Never pre-escaped by the caller. */
  query: string;
  /** Clamped to 1..MAX_SEARCH_LIMIT; omitted means DEFAULT_SEARCH_LIMIT. */
  limit?: number;
}

export interface SearchHit {
  conversationId: string;
  /** The conversation's title, so a result identifies itself without a second lookup. */
  title: string;
  messageId: string;
  role: Role;
  /**
   * A short excerpt around the match. Matched runs are wrapped in
   * SNIPPET_MATCH_OPEN/CLOSE, which are private-use code points rather than
   * markup: the renderer splits on them and renders text nodes, so a snippet
   * can never inject markup no matter what the conversation contained.
   */
  snippet: string;
  createdAt: number;
  /** FTS5 bm25 relevance. Lower is a better match; hits arrive already sorted. */
  score: number;
}

export interface SearchResult {
  /** Best match first. Empty for an empty or non-indexable query -- never all conversations. */
  hits: SearchHit[];
  /** True when more matches existed than the limit allowed. */
  truncated: boolean;
  /** Set only when the search could not be completed; hits is then empty. */
  error?: AppError;
}

/** Delimiters around matched runs inside SearchHit.snippet. */
export const SNIPPET_MATCH_OPEN = '\uE000';
export const SNIPPET_MATCH_CLOSE = '\uE001';

export const DEFAULT_SEARCH_LIMIT = 50;
export const MAX_SEARCH_LIMIT = 200;

export type ExportFormat = 'markdown' | 'json';

export interface ExportRequest {
  /** Conversation to export. */
  id: string;
  format: ExportFormat;
  /** Include the reasoning/thinking text alongside each answer. */
  includeReasoning: boolean;
}

/**
 * Cancelling a save dialog is a normal outcome, not a failure — it is reported
 * distinctly so the renderer never shows an error for a deliberate action.
 */
export type ExportResult =
  | { status: 'saved'; path: string; bytes: number }
  | { status: 'cancelled' }
  | { status: 'error'; message: string };

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
