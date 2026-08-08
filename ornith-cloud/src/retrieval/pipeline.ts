import type { Config } from '../config.js';
import { log } from '../log.js';
import { createCache, type Cache } from './cache.js';
import { extractContent } from './extract.js';
import { rankResults } from './rank.js';
import type {
  FetchProvider,
  RetrievalResult,
  RetrievedSource,
  SearchProvider,
  SearchResult,
} from './types.js';

export interface RetrievalDeps {
  search: SearchProvider;
  fetcher: FetchProvider;
  config: Config;
  now?: () => number;
}

export interface Retriever {
  retrieve(query: string, signal?: AbortSignal): Promise<RetrievalResult>;
  readonly caches: { search: Cache<SearchResult[]>; document: Cache<RetrievedSource> };
}

function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

export function createRetriever(deps: RetrievalDeps): Retriever {
  const { config } = deps;
  const now = deps.now ?? (() => Date.now());

  const searchCache = createCache<SearchResult[]>(
    config.cache.searchTtlMs,
    config.cache.maxEntries,
    now,
  );
  const documentCache = createCache<RetrievedSource>(
    config.cache.documentTtlMs,
    config.cache.maxEntries,
    now,
  );

  async function fetchOne(
    result: SearchResult,
    signal?: AbortSignal,
  ): Promise<RetrievedSource | null> {
    const cached = documentCache.get(result.url);
    if (cached) {
      // Report it as cached so the model can qualify freshness honestly.
      return { ...cached.value, fromCache: true };
    }

    try {
      const doc = await deps.fetcher.fetch(result.url, signal);
      const extracted = extractContent(doc.html, result.url, config.limits.maxDocumentChars);

      const source: RetrievedSource = {
        title: extracted.title || result.title,
        url: result.url,
        domain: domainOf(result.url),
        snippet: result.snippet,
        text: extracted.text,
        published: result.published,
        truncated: extracted.truncated || doc.truncated,
        fromCache: false,
        retrievedAt: new Date(now()).toISOString(),
      };

      documentCache.set(result.url, source);
      return source;
    } catch (err) {
      log.warn('retrieval.fetch_failed', {
        domain: domainOf(result.url),
        detail: err instanceof Error ? err.message.slice(0, 160) : String(err),
      });
      return null;
    }
  }

  return {
    caches: { search: searchCache, document: documentCache },

    async retrieve(query: string, signal?: AbortSignal): Promise<RetrievalResult> {
      const warnings: string[] = [];
      const searchStart = now();

      let results: SearchResult[] = [];
      const cachedSearch = searchCache.get(query);

      if (cachedSearch) {
        results = cachedSearch.value;
        log.debug('retrieval.cache_hit', { kind: 'search' });
      } else {
        try {
          results = await deps.search.search(query, {
            limit: config.limits.searchResults,
            signal,
          });
          searchCache.set(query, results);
        } catch (err) {
          warnings.push('search-failed');
          log.warn('retrieval.search_failed', {
            provider: deps.search.name,
            detail: err instanceof Error ? err.message.slice(0, 160) : String(err),
          });
        }
      }

      const searchMs = now() - searchStart;

      if (results.length === 0) {
        return { sources: [], searchMs, fetchMs: 0, warnings };
      }

      const selected = rankResults(results, query, config.limits.documentsFetched);

      // Independent network calls run concurrently; serialising them is the
      // single biggest avoidable latency cost in this pipeline.
      const fetchStart = now();
      const fetched = await Promise.all(selected.map((r) => fetchOne(r, signal)));
      const fetchMs = now() - fetchStart;

      const sources: RetrievedSource[] = [];
      let total = 0;

      for (const source of fetched) {
        if (!source) {
          warnings.push('fetch-failed');
          continue;
        }
        // Enforce the aggregate ceiling, trimming the last source if needed.
        const remaining = config.limits.maxTotalContextChars - total;
        if (remaining <= 0) {
          warnings.push('context-budget-exhausted');
          break;
        }
        if (source.text.length > remaining) {
          sources.push({ ...source, text: source.text.slice(0, remaining), truncated: true });
          total = config.limits.maxTotalContextChars;
        } else {
          sources.push(source);
          total += source.text.length;
        }
      }

      log.info('retrieval.done', {
        searchMs,
        fetchMs,
        found: results.length,
        used: sources.length,
        chars: total,
      });

      return { sources, searchMs, fetchMs, warnings };
    },
  };
}
