import type { SearchOptions, SearchProvider, SearchResult } from './types.js';

/**
 * Hosted search adapter (Brave Search API).
 *
 * Written against Brave's documented REST contract: GET /res/v1/web/search with
 * an `X-Subscription-Token` header, returning `{ web: { results: [...] } }`.
 * Nothing above this file names Brave — the retrieval pipeline only sees
 * SearchProvider, so Tavily/Serper/etc. are drop-in siblings.
 *
 * NOT VERIFIED AGAINST THE LIVE API: no credentials are available in this
 * environment. The normalisation logic is unit-tested against recorded-shape
 * payloads; the network call itself is unexercised.
 */

const BRAVE_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search';

interface BraveResult {
  title?: string;
  url?: string;
  description?: string;
  age?: string;
  page_age?: string;
  meta_url?: { hostname?: string };
}

interface BraveResponse {
  web?: { results?: BraveResult[] };
}

/** Exported for tests: normalisation is the part worth pinning down. */
export function normaliseBrave(body: unknown, limit: number): SearchResult[] {
  const parsed = (body ?? {}) as BraveResponse;
  const results = parsed.web?.results ?? [];

  return results
    .filter((r) => typeof r.url === 'string' && /^https?:\/\//i.test(r.url))
    .slice(0, limit)
    .map((r) => ({
      title: (r.title ?? '').trim() || r.url!,
      url: r.url!,
      snippet: (r.description ?? '').replace(/<[^>]+>/g, '').trim(),
      source: r.meta_url?.hostname,
      published: r.page_age ?? r.age,
    }));
}

export function createHostedSearchProvider(apiKey: string, timeoutMs: number): SearchProvider {
  return {
    name: 'brave',

    async search(query: string, options: SearchOptions = {}): Promise<SearchResult[]> {
      const limit = options.limit ?? 5;
      const url = new URL(BRAVE_ENDPOINT);
      url.searchParams.set('q', query);
      url.searchParams.set('count', String(Math.min(limit, 20)));
      url.searchParams.set('safesearch', 'moderate');

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const onAbort = () => controller.abort();
      options.signal?.addEventListener('abort', onAbort, { once: true });

      try {
        const res = await fetch(url, {
          signal: controller.signal,
          headers: {
            Accept: 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': apiKey,
          },
        });

        if (res.status === 401 || res.status === 403) {
          throw new Error('search provider rejected the API key');
        }
        if (res.status === 429) {
          throw new Error('search provider rate limited');
        }
        if (!res.ok) {
          throw new Error(`search returned HTTP ${res.status}`);
        }

        return normaliseBrave(await res.json(), limit);
      } finally {
        clearTimeout(timer);
        options.signal?.removeEventListener('abort', onAbort);
      }
    },
  };
}
