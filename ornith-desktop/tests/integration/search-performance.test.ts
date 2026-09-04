import { performance } from 'node:perf_hooks';
import type { DatabaseSync } from 'node:sqlite';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { openDatabase } from '../../electron/store/db';
import { createConversationStore, SEARCH_SQL, type ConversationStore } from '../../electron/store/conversations';
import { SNIPPET_MATCH_OPEN, SNIPPET_MATCH_CLOSE } from '../../shared/types';
import { generateSearchCorpus, insertSearchCorpus } from '../fixtures/searchCorpus';

/**
 * Performance regression coverage for `ConversationStore.search`, against a
 * deterministic seeded corpus (see tests/fixtures/searchCorpus.ts) at
 * roughly the scale the Lead measured (~540 conversations / ~5,600
 * messages).
 *
 * Two independent kinds of assertion, deliberately not conflated:
 *
 *  (a) STRUCTURAL (`EXPLAIN QUERY PLAN`) -- the one with real teeth. Confirms
 *      the search query is served by the FTS5 virtual tables, not a full
 *      scan of the base tables. This is what actually catches the regression
 *      that matters: someone replacing FTS5 with `LIKE`.
 *  (b) WALL-CLOCK -- a generous backstop. See the comment above
 *      SINGLE_QUERY_MS_LIMIT for exactly what it does and does not catch.
 *
 * What this file does NOT claim: search is not asserted to be "fast",
 * "scalable", or "production-ready" anywhere below -- only what was measured,
 * on what corpus, in what runtime. The corpus here is generated in-process by
 * this test, not the Lead's original corpus; the Lead's numbers (cited below)
 * are the basis for deriving a threshold, not a target this suite claims to
 * reproduce.
 */

describe('search performance', () => {
  let db: DatabaseSync;
  let store: ConversationStore;

  beforeAll(() => {
    db = openDatabase(':memory:');
    store = createConversationStore(db);

    const corpus = generateSearchCorpus();

    const writeStart = performance.now();
    insertSearchCorpus(db, corpus);
    const writeMs = performance.now() - writeStart;

    // Informational only -- not asserted. There is no verified basis in this
    // worktree for a specific build-time threshold on this generator's
    // output; this is reported for the Lead's review, not claimed as a
    // regression gate.
    console.log(
      `[search-perf] corpus: ${corpus.conversations.length} conversations, ` +
        `${corpus.messages.length} messages, write=${writeMs.toFixed(1)}ms`,
    );
  });

  afterAll(() => {
    db.close();
  });

  describe('structural: EXPLAIN QUERY PLAN', () => {
    it('serves the combined title+content search through the FTS5 indexes, with no base-table scan', () => {
      // Bound directly to an already-valid, pre-quoted FTS5 phrase-prefix
      // expression (mirroring what the store's own (private, unexported)
      // query-escaping produces for a plain word) rather than reimplementing
      // that escaping here: EXPLAIN QUERY PLAN's chosen access path for an
      // FTS5 MATCH constraint is structural (there is no alternative access
      // path for a virtual table's MATCH constraint), not dependent on which
      // specific term is searched, so the exact bound value does not need to
      // match production's escaping byte-for-byte for this assertion to be
      // meaningful.
      const matchQuery = '"heron"*';

      const plan = db
        .prepare(`EXPLAIN QUERY PLAN ${SEARCH_SQL}`)
        .all(
          SNIPPET_MATCH_OPEN,
          SNIPPET_MATCH_CLOSE,
          matchQuery,
          SNIPPET_MATCH_OPEN,
          SNIPPET_MATCH_CLOSE,
          matchQuery,
          // P2P-DEFECT-2 suppression subquery's own MATCH bind -- same
          // escaped value as the title branch's MATCH above it. Mirrors
          // electron/store/conversations.ts's searchStatement.all() bind
          // order exactly; this EXPLAIN is run against that exact SQL text,
          // not a hand-duplicated copy, so the bind list must match it
          // positionally or this validates the wrong plan.
          matchQuery,
          50,
        ) as unknown as Array<{ detail: string }>;

      const combined = plan.map((row) => row.detail).join('\n');

      // The FTS5 index is actually consulted for both indexes. The `:M<n>`
      // suffix on a virtual-table scan is SQLite's signal that the MATCH
      // constraint is served by the FTS5 index, rather than the virtual
      // table being iterated unindexed -- confirmed by the Lead against a
      // reconstructed schema before this test was written; re-derived here
      // as a pattern rather than the Lead's literal string, per their
      // instruction not to hardcode it.
      expect(combined).toMatch(/SCAN messages_fts VIRTUAL TABLE INDEX \d+:M\d+/);
      expect(combined).toMatch(/SCAN conversations_fts VIRTUAL TABLE INDEX \d+:M\d+/);

      // No full scan of either BASE table. `\bmessages\b` / `\bconversations\b`
      // do not match inside `messages_fts` / `conversations_fts`: `_` is a
      // word character, so there is no word boundary between the base name
      // and the `_fts` suffix. This is what a LIKE-based regression would
      // trip: the Lead's own reconstruction of that regression produced a
      // bare `SCAN c` (a full scan of the conversations table).
      expect(combined).not.toMatch(/SCAN\s+messages\b/i);
      expect(combined).not.toMatch(/SCAN\s+conversations\b/i);

      // SEARCH ... USING INTEGER PRIMARY KEY (rowid=?) / USING INDEX ... are
      // expected and fine: those are the rowid/PK joins back to the base
      // tables to fetch role/created_at, keyed lookups rather than scans.
      // Asserting their presence isn't required for this test's claim, so
      // they are only noted here, not asserted on.
    });
  });

  describe('wall-clock backstop', () => {
    /**
     * Derivation (do not read this as a claim of these exact numbers in this
     * environment -- see the file-level comment above):
     *
     * The Lead's reference measurements (Electron 39.8.10 / Node 22.22.1, a
     * corpus of ~540 conversations / ~5,600 messages):
     *   - worst cold query:   6.37 ms ('a-b', punctuation)
     *   - worst warm p95:     4.80 ms
     *   - worst warm max:     5.62 ms
     *   - typical warm p50:   0.03-2.8 ms
     *
     * SINGLE_QUERY_MS_LIMIT = 200 ms is a large, deliberately generous
     * multiple of every one of those numbers (~31x the worst cold
     * measurement, ~36x the worst warm max, ~42x the worst warm p95), chosen
     * so a slower or contended CI box cannot flake this suite.
     *
     * What this catches: catastrophic, plan-independent regressions --  an
     * accidental O(n^2) loop, an unbounded join fan-out, an N+1 pattern
     * issuing hundreds of statements per search, or synchronous re-indexing
     * triggered on every keystroke.
     *
     * What this deliberately does NOT catch: an FTS5 -> LIKE substitution. At
     * this corpus size (~5,600 rows) a `LIKE '%term%'` scan can plausibly
     * still finish well under 200 ms, so this generous a wall-clock cap alone
     * would not flag that regression. That is exactly why the structural
     * EXPLAIN QUERY PLAN assertions above exist as a separate, non-negotiable
     * check -- this timer is not a substitute for them.
     *
     * This suite's own timings are not asserted against the Lead's specific
     * numbers (only against this one generous ceiling): this test runs a
     * different corpus (generated here, not the Lead's), in a different
     * process (plain node:sqlite via vitest, not the real Electron
     * renderer/IPC round trip the Lead measured), so a tighter number here
     * would not be a like-for-like comparison.
     */
    const SINGLE_QUERY_MS_LIMIT = 200;

    // A representative spread: a plain single word, a multi-word phrase, the
    // Lead's own cited worst case, a query with no matches (still a real
    // FTS5 MATCH execution against a populated index -- not vacuous, since
    // this corpus is non-empty), and several of the hazardous strings
    // tests/unit/search.test.ts exercises for correctness, run here purely
    // for their timing characteristics.
    const QUERIES = ['heron', 'survey wetland', 'a-b', 'zzznomatchforthisz', '"', 'AND', '*'];

    it('every query, cold and warm, completes within the generous wall-clock ceiling', () => {
      for (const query of QUERIES) {
        const coldStart = performance.now();
        store.search({ query });
        const coldMs = performance.now() - coldStart;
        expect(coldMs).toBeLessThan(SINGLE_QUERY_MS_LIMIT);

        const warmTimings: number[] = [];
        for (let i = 0; i < 50; i += 1) {
          const start = performance.now();
          store.search({ query });
          warmTimings.push(performance.now() - start);
        }

        const max = Math.max(...warmTimings);
        expect(max).toBeLessThan(SINGLE_QUERY_MS_LIMIT);

        const sorted = [...warmTimings].sort((a, b) => a - b);
        const p50 = sorted[Math.floor(sorted.length * 0.5)];
        const p95 = sorted[Math.floor(sorted.length * 0.95)];

        // Informational only -- see the file-level comment: not asserted
        // against the Lead's specific figures, only reported for review.
        console.log(
          `[search-perf] query=${JSON.stringify(query)} cold=${coldMs.toFixed(2)}ms ` +
            `warm p50=${p50.toFixed(2)}ms p95=${p95.toFixed(2)}ms max=${max.toFixed(2)}ms`,
        );
      }
    });
  });
});
