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

/**
 * Which index produced a hit.
 *
 * v2 adds title matching. It is a SECOND external-content FTS5 table over
 * `conversations.title` rather than an extension of the message index, because
 * external content maps every indexed column to one base table — verified:
 * adding a `title` column to the messages index makes `rebuild` fail with
 * `no such column: T.title`.
 *
 * A non-FTS title match (LIKE) was measured and rejected: it loses diacritic
 * folding (`cafe` stops finding `café`), it has no bm25 and no snippet, and it
 * substring-matches punctuation, so a query of `"` alone would return title
 * hits — breaking the guarantee that a non-indexable query returns nothing.
 */
export type SearchMatchField = 'title' | 'content';

export interface SearchHit {
  conversationId: string;
  /** The conversation's title, so a result identifies itself without a second lookup. */
  title: string;
  /** Which index matched. Both kinds carry conversationId and title. */
  matchedIn: SearchMatchField;
  /**
   * Present only on a content match. A title match is a property of the
   * conversation and is not tied to any one message, so these are absent
   * rather than filled with a placeholder.
   */
  messageId?: string;
  role?: Role;
  /**
   * A short excerpt around the match. Matched runs are wrapped in
   * SNIPPET_MATCH_OPEN/CLOSE, which are private-use code points rather than
   * markup: the renderer splits on them and renders text nodes, so a snippet
   * can never inject markup no matter what the conversation contained.
   */
  /**
   * For a content match, an excerpt of the message. For a title match, an
   * excerpt of the title. Match runs are delimited the same way in both.
   */
  snippet: string;
  /** Message time for a content match; conversation update time for a title match. */
  createdAt: number;
  /**
   * FTS5 bm25 relevance. Lower is a better match; hits arrive already sorted.
   *
   * Scores from the title and content indexes are computed over different
   * corpora and are therefore not strictly comparable to each other. Ordering
   * within one kind of match is meaningful; the relative position of a title
   * hit against a content hit is approximate, and deliberately not presented
   * to the user as a precise ranking.
   */
  score: number;
}

export interface SearchResult {
  /**
   * Best match first, across both indexes. Empty for an empty or
   * non-indexable query -- never all conversations.
   */
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
