/**
 * Conversation search — the privileged half of the search feature.
 *
 * SPEC.md §11.3 treats every renderer→main payload as untrusted, so the
 * request is validated field by field before anything downstream relies on
 * it, mirroring the same defensive pattern `electron/export.ts` uses for
 * `conv:export`. The actual FTS5 query lives in
 * `electron/store/conversations.ts` (`ConversationStore.search`); this module
 * only validates the untrusted input and translates an unexpected failure
 * into a safe `SearchResult` — it never reimplements or reshapes what the
 * store returns.
 *
 * Testability seam: this module has no Electron import at all, so
 * `searchConversations` can be exercised in a test against a real store (or
 * a narrow fake of one) without launching Electron.
 */
import type { SearchRequest, SearchResult } from '../shared/types';
import type { ConversationStore } from './store/conversations';
import { log } from './log';

/** All this module needs from the store — narrow, so tests can supply a fake. */
export type ConversationSearcher = Pick<ConversationStore, 'search'>;

export interface SearchDeps {
  store: ConversationSearcher;
}

/**
 * The renderer is untrusted (SPEC.md §11.3), so the shape of `request` is
 * checked before anything touches the store — mirroring `isValidRequest` in
 * `electron/export.ts`. `limit` is checked separately by `normalizeLimit`
 * since an invalid `limit` should not invalidate an otherwise-valid query.
 */
function isValidRequest(value: unknown): value is { query: string; limit?: unknown } {
  if (typeof value !== 'object' || value === null) return false;
  const req = value as Record<string, unknown>;
  return typeof req.query === 'string';
}

/**
 * `limit` is optional and the store already clamps it (see
 * `clampSearchLimit` in `electron/store/conversations.ts`), so this only
 * decides whether to pass a caller-supplied value through at all. A present
 * but wrong-typed `limit` (including `NaN`, which is still `typeof
 * 'number'`) is handed to the store exactly like any other number — the
 * store already treats `NaN` as "use the default" — and anything that is not
 * a number at all is treated as if `limit` had been omitted, rather than
 * rejecting the whole request over one bad field.
 */
function normalizeLimit(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

/**
 * Validates an untrusted search request and delegates to the store. Never
 * throws: a malformed request resolves to a well-formed, empty
 * `SearchResult` exactly as an empty query does in the store, and a store
 * failure (including a store whose `search` itself throws, rather than
 * reporting failure via `SearchResult.error`) resolves to a `SearchResult`
 * carrying an `error` instead of propagating.
 */
export function searchConversations(request: unknown, deps: SearchDeps): SearchResult {
  if (!isValidRequest(request)) {
    log.warn('search.invalid_request');
    return { hits: [], truncated: false };
  }

  const searchRequest: SearchRequest = {
    query: request.query,
    limit: normalizeLimit(request.limit),
  };

  try {
    return deps.store.search(searchRequest);
  } catch (err) {
    // Never let a raw error (path, SQL, stack) reach the renderer; only the
    // detail is logged, never rendered.
    log.error('search.failed', { detail: String(err) });
    return {
      hits: [],
      truncated: false,
      error: {
        code: 'STORAGE_CORRUPT',
        message: 'Search is unavailable right now. Try restarting the app.',
      },
    };
  }
}
