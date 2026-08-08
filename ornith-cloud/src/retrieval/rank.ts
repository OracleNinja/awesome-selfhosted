import type { SearchResult } from './types.js';

/**
 * Deterministic ranking. No model call — this runs before the model and its
 * whole purpose is to keep the expensive step small.
 *
 * Pure and fully unit-tested.
 */

const STOP_WORDS = new Set([
  'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'to', 'in', 'on', 'for',
  'and', 'or', 'what', 'whats', 'how', 'why', 'when', 'who', 'it', 'its',
  'this', 'that', 'with', 'about', 'current', 'latest', 'today',
]);

export function tokenise(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .split(/\s+/)
    .filter((t) => t.length > 1 && !STOP_WORDS.has(t));
}

/** Domains that tend to be low-signal for factual retrieval. */
const DEMOTED = /(pinterest|quora|facebook|instagram|tiktok)\./i;

export function scoreResult(result: SearchResult, queryTerms: string[]): number {
  const haystack = `${result.title} ${result.snippet}`.toLowerCase();
  let score = 0;

  for (const term of queryTerms) {
    if (haystack.includes(term)) score += 2;
    if (result.title.toLowerCase().includes(term)) score += 1; // title weighs more
  }

  // Prefer results the search engine dated recently.
  if (result.published) {
    const age = Date.now() - Date.parse(result.published);
    if (Number.isFinite(age) && age >= 0) {
      const days = age / 86_400_000;
      if (days < 2) score += 4;
      else if (days < 14) score += 2;
      else if (days < 90) score += 1;
    }
  }

  if (DEMOTED.test(result.url)) score -= 3;
  if (!result.snippet) score -= 1;

  return score;
}

/** Ranks and de-duplicates by hostname so one site cannot occupy every slot. */
export function rankResults(
  results: readonly SearchResult[],
  query: string,
  limit: number,
): SearchResult[] {
  const terms = tokenise(query);

  const scored = results
    .map((result, index) => ({ result, score: scoreResult(result, terms), index }))
    // Stable: equal scores keep the search engine's own ordering.
    .sort((a, b) => b.score - a.score || a.index - b.index);

  const seenHosts = new Set<string>();
  const picked: SearchResult[] = [];

  for (const { result } of scored) {
    let host: string;
    try {
      host = new URL(result.url).hostname.replace(/^www\./, '');
    } catch {
      continue;
    }
    if (seenHosts.has(host)) continue;
    seenHosts.add(host);
    picked.push(result);
    if (picked.length >= limit) break;
  }

  return picked;
}
