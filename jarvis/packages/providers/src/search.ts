/**
 * Web search providers backing the `web_search` tool.
 *
 * Three real adapters (SearXNG, Brave, Tavily) and one honest "not configured"
 * implementation. Nothing here ever invents results: when no provider is
 * configured, the tool tells the model and the user that web search is
 * unavailable rather than answering from the model's own recollection.
 */
import type { JarvisConfig, ProviderStatus } from '@jarvis/shared';
import { fetchWithTimeout, safeHost, truncate } from '@jarvis/shared';
import { ProviderError, ProviderUnavailableError } from './types.ts';

export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

export interface SearchProvider {
  readonly id: string;
  isAvailable(): boolean;
  status(): ProviderStatus;
  search(query: string, options?: { limit?: number; signal?: AbortSignal }): Promise<SearchResult[]>;
}

export class UnavailableSearchProvider implements SearchProvider {
  readonly id = 'search:none';
  constructor(private readonly reason: string) {}

  isAvailable(): boolean {
    return false;
  }

  status(): ProviderStatus {
    return { id: this.id, kind: 'search', available: false, reason: this.reason };
  }

  async search(): Promise<SearchResult[]> {
    throw new ProviderUnavailableError(this.id, this.reason);
  }
}

abstract class HttpSearchProvider implements SearchProvider {
  abstract readonly id: string;
  protected abstract endpoint(): string;
  protected abstract unavailableReason(): string | null;
  protected abstract request(
    query: string,
    limit: number,
    signal?: AbortSignal,
  ): Promise<SearchResult[]>;

  isAvailable(): boolean {
    return this.unavailableReason() === null;
  }

  status(): ProviderStatus {
    const reason = this.unavailableReason();
    const status: ProviderStatus = {
      id: this.id,
      kind: 'search',
      available: reason === null,
      endpoint: safeHost(this.endpoint()),
    };
    if (reason) status.reason = reason;
    return status;
  }

  async search(
    query: string,
    options: { limit?: number; signal?: AbortSignal } = {},
  ): Promise<SearchResult[]> {
    const reason = this.unavailableReason();
    if (reason) throw new ProviderUnavailableError(this.id, reason);
    return this.request(query, options.limit ?? 5, options.signal);
  }

  protected async getJson(
    url: string,
    headers: Record<string, string>,
    signal?: AbortSignal,
  ): Promise<unknown> {
    let response: Response;
    try {
      response = await fetchWithTimeout(url, {
        headers: { accept: 'application/json', ...headers },
        timeoutMs: 20_000,
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      throw new ProviderError(this.id, `search request failed: ${(error as Error).message}`, {
        retryable: true,
      });
    }
    const text = await response.text();
    if (!response.ok) {
      throw new ProviderError(this.id, `HTTP ${response.status}: ${truncate(text, 200)}`, {
        status: response.status,
      });
    }
    try {
      return JSON.parse(text);
    } catch {
      throw new ProviderError(this.id, 'search endpoint returned invalid JSON');
    }
  }
}

export class SearxngSearchProvider extends HttpSearchProvider {
  readonly id = 'searxng';
  constructor(private readonly baseUrl: string) {
    super();
  }

  protected endpoint(): string {
    return this.baseUrl;
  }

  protected unavailableReason(): string | null {
    return this.baseUrl ? null : 'searxng: SEARXNG_URL is not configured';
  }

  protected async request(query: string, limit: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const url = `${this.baseUrl.replace(/\/+$/, '')}/search?q=${encodeURIComponent(query)}&format=json`;
    const payload = (await this.getJson(url, {}, signal)) as {
      results?: { title?: string; url?: string; content?: string }[];
    };
    return (payload.results ?? []).slice(0, limit).map((result) => ({
      title: result.title ?? '',
      url: result.url ?? '',
      snippet: truncate(result.content ?? '', 400),
    }));
  }
}

export class BraveSearchProvider extends HttpSearchProvider {
  readonly id = 'brave';
  constructor(private readonly apiKey: string) {
    super();
  }

  protected endpoint(): string {
    return 'https://api.search.brave.com';
  }

  protected unavailableReason(): string | null {
    return this.apiKey ? null : 'brave: BRAVE_API_KEY is not configured';
  }

  protected async request(query: string, limit: number, signal?: AbortSignal): Promise<SearchResult[]> {
    const url = `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=${limit}`;
    const payload = (await this.getJson(url, { 'x-subscription-token': this.apiKey }, signal)) as {
      web?: { results?: { title?: string; url?: string; description?: string }[] };
    };
    return (payload.web?.results ?? []).slice(0, limit).map((result) => ({
      title: result.title ?? '',
      url: result.url ?? '',
      snippet: truncate((result.description ?? '').replace(/<[^>]+>/g, ''), 400),
    }));
  }
}

export class TavilySearchProvider extends HttpSearchProvider {
  readonly id = 'tavily';
  constructor(private readonly apiKey: string) {
    super();
  }

  protected endpoint(): string {
    return 'https://api.tavily.com';
  }

  protected unavailableReason(): string | null {
    return this.apiKey ? null : 'tavily: TAVILY_API_KEY is not configured';
  }

  protected async request(query: string, limit: number, signal?: AbortSignal): Promise<SearchResult[]> {
    let response: Response;
    try {
      response = await fetchWithTimeout('https://api.tavily.com/search', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ api_key: this.apiKey, query, max_results: limit }),
        timeoutMs: 20_000,
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      throw new ProviderError(this.id, `search request failed: ${(error as Error).message}`, {
        retryable: true,
      });
    }
    const text = await response.text();
    if (!response.ok) {
      throw new ProviderError(this.id, `HTTP ${response.status}: ${truncate(text, 200)}`, {
        status: response.status,
      });
    }
    const payload = JSON.parse(text) as {
      results?: { title?: string; url?: string; content?: string }[];
    };
    return (payload.results ?? []).slice(0, limit).map((result) => ({
      title: result.title ?? '',
      url: result.url ?? '',
      snippet: truncate(result.content ?? '', 400),
    }));
  }
}

export function createSearchProvider(config: JarvisConfig): SearchProvider {
  switch (config.search.provider) {
    case 'searxng':
      return new SearxngSearchProvider(config.search.searxngUrl);
    case 'brave':
      return new BraveSearchProvider(config.search.braveApiKey);
    case 'tavily':
      return new TavilySearchProvider(config.search.tavilyApiKey);
    case 'none':
    default:
      return new UnavailableSearchProvider(
        'Web search is not configured. Set SEARCH_PROVIDER to searxng, brave or tavily and supply its endpoint or key.',
      );
  }
}
