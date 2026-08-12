import type { FetchProvider, FetchedDocument } from './types.js';

/** Hosts and schemes that must never be fetched on a model's behalf. */
const PRIVATE_HOST = /^(localhost|127\.|0\.|10\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|\[::1\]|.*\.local)$/i;

export function isFetchable(rawUrl: string): boolean {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
  // SSRF guard: retrieved links are attacker-influenced, so private ranges and
  // link-local addresses are refused outright.
  if (PRIVATE_HOST.test(url.hostname)) return false;
  return true;
}

export function createHttpFetchProvider(timeoutMs: number, maxBytes: number): FetchProvider {
  return {
    name: 'http',

    async fetch(rawUrl: string, signal?: AbortSignal): Promise<FetchedDocument> {
      if (!isFetchable(rawUrl)) {
        throw new Error('URL is not fetchable');
      }

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const onAbort = () => controller.abort();
      signal?.addEventListener('abort', onAbort, { once: true });

      try {
        const res = await fetch(rawUrl, {
          signal: controller.signal,
          redirect: 'follow',
          headers: {
            // Identify honestly rather than impersonating a browser.
            'User-Agent': 'OrnithCloud/0.1 (+https://github.com/OracleNinja)',
            Accept: 'text/html,application/xhtml+xml',
          },
        });

        const contentType = res.headers.get('content-type') ?? '';
        if (!/text\/html|application\/xhtml/i.test(contentType)) {
          throw new Error(`unsupported content-type: ${contentType || 'unknown'}`);
        }

        // Read with a hard byte ceiling so one huge page cannot exhaust memory.
        const reader = res.body?.getReader();
        if (!reader) throw new Error('empty response body');

        const decoder = new TextDecoder('utf-8');
        let html = '';
        let bytes = 0;
        let truncated = false;

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (bytes > maxBytes) {
            truncated = true;
            await reader.cancel().catch(() => {});
            break;
          }
          html += decoder.decode(value, { stream: true });
        }

        return { url: rawUrl, status: res.status, contentType, html, truncated };
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', onAbort);
      }
    },
  };
}
