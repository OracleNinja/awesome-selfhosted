import type { Settings } from './types';

export const DEFAULT_MODEL = 'ornith-en';
export const DEFAULT_OLLAMA_URL = 'http://localhost:11434';

export const DEFAULT_SETTINGS: Settings = {
  schemaVersion: 1,
  ollamaUrl: DEFAULT_OLLAMA_URL,
  model: DEFAULT_MODEL,
  // Ornith model card recommendation.
  temperature: 0.6,
  topP: 0.95,
  // SPEC C5: Ollama defaults num_ctx to 4096 and silently truncates. Always explicit.
  numCtx: 8192,
  keepAlive: '15m',
  theme: 'system',
  language: 'en',
  showThinkingByDefault: false,
  sendOnEnter: true,

  // Off by default: typing a message and having the Mac start talking is a
  // surprise. Replies to spoken prompts are spoken regardless of this setting.
  speakResponses: false,
  voiceName: '',
  speechRate: 175,
  sttLocale: 'en-US',
};

/** Field bounds, used by settings validation and by the settings dialog. */
export const SETTINGS_BOUNDS = {
  temperature: { min: 0, max: 2 },
  topP: { min: 0, max: 1 },
  numCtx: { min: 2048, max: 131072 },
  speechRate: { min: 80, max: 400 },
} as const;

/** Conservative for code-heavy text; deliberately over-estimates tokens. */
export const CHARS_PER_TOKEN = 3.5;

/** Fraction of the context window reserved for the response. */
export const RESPONSE_RESERVE_RATIO = 0.25;

/** No token within this window means the model is still loading into RAM. */
export const MODEL_LOADING_THRESHOLD_MS = 2000;

/** Delta batching window (SPEC C3). */
export const COALESCE_INTERVAL_MS = 50;
export const COALESCE_MAX_BYTES = 2048;

/** Incremental persistence cadence during streaming. */
export const STREAM_PERSIST_INTERVAL_MS = 250;

export const STATUS_POLL_INTERVAL_MS = 15_000;
export const PROBE_TIMEOUT_MS = 4000;
