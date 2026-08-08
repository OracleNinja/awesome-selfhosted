/**
 * Configuration from the environment. Secrets live only here and are never
 * echoed back to a client or written to a log.
 */

function num(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function bool(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  return value === '1' || value.toLowerCase() === 'true';
}

export interface Config {
  port: number;
  /** Bearer token the desktop must present. Empty disables the server. */
  gatewayToken: string;
  aiProvider: 'anthropic' | 'stub';
  anthropicApiKey: string;
  onlineModel: string;
  fastModel: string;
  maxOutputTokens: number;

  searchProvider: 'hosted' | 'searxng' | 'none';
  searxngUrl: string;
  searchApiKey: string;

  timeouts: {
    searchMs: number;
    fetchMs: number;
    providerMs: number;
  };

  limits: {
    searchResults: number;
    documentsFetched: number;
    maxDocumentChars: number;
    maxTotalContextChars: number;
  };

  cache: {
    searchTtlMs: number;
    documentTtlMs: number;
    maxEntries: number;
  };

  logLevel: 'error' | 'warn' | 'info' | 'debug';
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const provider = env.AI_PROVIDER === 'stub' ? 'stub' : 'anthropic';

  return {
    port: num(env.PORT, 8787),
    gatewayToken: env.ORNITH_GATEWAY_TOKEN ?? '',
    aiProvider: provider,
    anthropicApiKey: env.ANTHROPIC_API_KEY ?? '',
    onlineModel: env.ORNITH_ONLINE_MODEL ?? 'claude-sonnet-4-5',
    fastModel: env.ORNITH_FAST_MODEL ?? env.ORNITH_ONLINE_MODEL ?? 'claude-haiku-4-5',
    maxOutputTokens: num(env.ORNITH_MAX_OUTPUT_TOKENS, 2048),

    // Hosted search wins when a key is present; SearXNG is the self-hosted
    // alternative; neither means retrieval is disabled entirely.
    searchProvider: env.SEARCH_API_KEY ? 'hosted' : env.SEARXNG_URL ? 'searxng' : 'none',
    searxngUrl: (env.SEARXNG_URL ?? '').replace(/\/$/, ''),
    searchApiKey: env.SEARCH_API_KEY ?? '',

    timeouts: {
      searchMs: num(env.ORNITH_SEARCH_TIMEOUT_MS, 8000),
      fetchMs: num(env.ORNITH_FETCH_TIMEOUT_MS, 8000),
      providerMs: num(env.ORNITH_PROVIDER_TIMEOUT_MS, 60_000),
    },

    limits: {
      searchResults: num(env.ORNITH_SEARCH_RESULTS, 5),
      documentsFetched: num(env.ORNITH_DOCUMENTS_FETCHED, 3),
      maxDocumentChars: num(env.ORNITH_MAX_DOCUMENT_CHARS, 20_000),
      maxTotalContextChars: num(env.ORNITH_MAX_CONTEXT_CHARS, 50_000),
    },

    cache: {
      searchTtlMs: num(env.ORNITH_SEARCH_CACHE_MS, 60_000),
      documentTtlMs: num(env.ORNITH_DOCUMENT_CACHE_MS, 300_000),
      maxEntries: num(env.ORNITH_CACHE_MAX_ENTRIES, 500),
    },

    logLevel: (env.LOG_LEVEL as Config['logLevel']) ?? 'info',
  };
}

/** Startup validation. Returns human-readable problems; never includes values. */
export function validateConfig(config: Config): string[] {
  const problems: string[] = [];

  if (!config.gatewayToken) {
    problems.push('ORNITH_GATEWAY_TOKEN is not set. The server refuses to start without it.');
  } else if (config.gatewayToken.length < 24) {
    problems.push('ORNITH_GATEWAY_TOKEN is shorter than 24 characters. Use a long random value.');
  }

  if (config.aiProvider === 'anthropic' && !config.anthropicApiKey) {
    problems.push('AI_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set.');
  }

  return problems;
}

export const bool_ = bool; // exported for tests of the parser helpers
