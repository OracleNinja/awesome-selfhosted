import type { SearchOptions, SearchProvider, SearchResult } from './types.js';

/**
 * SearXNG adapter. Chosen as the reference implementation because it can be
 * self-hosted, which keeps queries off a third-party search API — but nothing
 * above this file knows SearXNG exists.
 */
export function createSearxngProvider(baseUrl: string, timeoutMs: number): SearchProvider {
  const root = baseUrl.replace(/\/$/, '');

  return {
    name: 'searxng',

    async search(query: string, options: SearchOptions = {}): Promise<SearchResult[]> {
      const limit = options.limit ?? 5;
      const url = new URL(`${root}/search`);
      url.searchParams.set('q', query);
      url.searchParams.set('format', 'json');
      url.searchParams.set('safesearch', '1');

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const onAbort = () => controller.abort();
      options.signal?.addEventListener('abort', onAbort, { once: true });

      try {
        const res = await fetch(url, {
          signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
        if (!res.ok) throw new Error(`search returned HTTP ${res.status}`);

        const body = (await res.json()) as {
          results?: Array<{
            title?: string;
            url?: string;
            content?: string;
            publishedDate?: string;
          }>;
        };

        return (body.results ?? [])
          .filter((r) => typeof r.url === 'string' && /^https?:\/\//i.test(r.url))
          .slice(0, limit)
          .map((r) => ({
            title: (r.title ?? '').trim() || r.url!,
            url: r.url!,
            snippet: (r.content ?? '').trim(),
            published: r.publishedDate,
          }));
      } finally {
        clearTimeout(timer);
        options.signal?.removeEventListener('abort', onAbort);
      }
    },
  };
}

/** Used when no search provider is configured. */
export function createNullSearchProvider(): SearchProvider {
  return {
    name: 'none',
    async search() {
      return [];
    },
  };
}
