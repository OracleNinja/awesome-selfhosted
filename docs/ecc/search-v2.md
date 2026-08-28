# Ornith Desktop Search v2 — what shipped, what was measured, what is still open

Phase 2P. Title matching added to conversation search, with performance coverage
justified by measurement rather than assertion.

## What shipped

A second FTS5 external-content index over `conversations.title`, unioned with the
existing message-content index in one statement so a single `ORDER BY` + `LIMIT`
decides the combined page. `SearchHit` gained `matchedIn: 'title' | 'content'`;
`messageId` and `role` became optional, present only on a content match.

Three confined workers, integrated in sequence through the manifest gate:

| | scope | landed |
|---|---|---|
| A | title index, migration to `user_version = 3`, backfill, store search v2 | `27169ae` |
| B | main-process IPC integration coverage (test-only, no production diff) | `ad47013` |
| C | renderer, e2e, deterministic-corpus performance coverage | `45151a3` |

## Measurements, not adjectives

Reference corpus, deterministic and seeded, in the real Electron 39.8.10 /
Node 22.22.1 runtime — 544 conversations / 5,611 messages:

- worst cold query 6.37 ms (`a-b`, punctuation); worst warm p95 4.80 ms; worst warm max 5.62 ms
- content index build 17.5 ms; title index build 2.2 ms; database 2.79 MB

The in-repo regression corpus (`tests/fixtures/searchCorpus.ts`, mulberry32, fixed
seed) generates 540 conversations / 5,665 messages and measured 0.00-4.86 ms per
query in plain `node:sqlite` under vitest.

Search is **not** claimed here to be fast, scalable, or production-ready. What is
claimed is the above: these queries, on that corpus, in those runtimes.

## Why the performance suite has two independent assertions

The wall-clock backstop is 200 ms — roughly 31x the worst cold measurement. It
catches catastrophic, plan-independent regressions (an accidental O(n^2), an
unbounded join fan-out, an N+1 issuing hundreds of statements per search).

It deliberately does **not** catch an FTS5 -> LIKE substitution, and this was
verified rather than assumed. Replacing `SEARCH_SQL` with a LIKE-based equivalent
and running the suite:

- the structural `EXPLAIN QUERY PLAN` assertions FAILED (correct)
- the wall-clock assertion PASSED, at 0.44 ms p50 — **LIKE was faster** than FTS5
  at this corpus size

So a timing-only performance test would not merely have been weak here, it would
have been actively misleading. The structural assertion is the one carrying the
weight; the timer is a backstop, not a substitute.

To make that assertion test production rather than a copy, the search SQL was
lifted verbatim out of the store closure into an exported `SEARCH_SQL` constant
(byte-identical, 1030 chars; `db.prepare(SEARCH_SQL)` is what production runs).

## P2P-DEFECT-1 — renderer key collision (fixed; severity corrected)

`src/App.tsx` keyed search rows on `hit.messageId`, which v2 made optional. Measured:
a query matching three conversation titles returned 3 hits with `messageId ===
undefined`, collapsing to **1 distinct key**.

The fix keys on `${matchedIn}-${messageId ?? conversationId}`.

**Correction, recorded deliberately.** The Lead first described this as a user-visible
correctness defect that could make a row open a different conversation than it
displays. That was then tested: reverting the fix and running the full Playwright
search spec against a rebuilt app left **all 10 tests passing**. The rows hold no
internal state and their content is fully derived from props, so React re-renders
them correctly even falling back to index-based reconciliation.

Honest position: a React contract violation, fixed on hygiene grounds, with **no
test proving the fix necessary** and no observed user-visible consequence. No test
was manufactured for it — the only signal is a development-mode warning that the
production build Playwright drives strips out.

## P2P-DEFECT-2 — one conversation returns two rows (DEFERRED to 2Q)

`electron/chat/orchestrator.ts:89` derives a conversation's title from its own first
user message. So a query matching a term in that opening message matches both
indexes for the same conversation, and the union emits a content row *and* a title
row for it. One conversation, two rows — in the common case, not an edge case.

Found by running the e2e baseline before Worker C changed anything; reproduced at
the store layer (one conversation, query `unicorn`, 2 hits, 1 distinct
conversationId).

This is a Lead error: the v2 contract unioned the two indexes without accounting for
how this app creates titles.

**Deferred to Phase 2Q by explicit decision.** Phase 2P's only change on its account
is disambiguating one e2e locator with `.first()`, every behavioural assertion in
that test preserved, plus a comment naming the defect. The test does not assert that
two rows exist — that would cement the defect and break when 2Q fixes it.

### Why 320 unit and integration tests missed it

Every unit and integration fixture deliberately isolates the title term from the
content term. That is good discipline for testing two indexes independently, and it
is exactly what guaranteed nobody tested the case the product actually produces.
Only the e2e layer, which seeds through the real app, caught it.

Carried into 2Q: add a fixture whose title and content **do** overlap, at the
unit/integration layer, so this class of defect cannot hide behind index-isolated
fixtures again.

## P2P-FINDING-1 — a concrete instance of the CI-only e2e divergence

`tests/e2e/app.spec.ts:325` ("labels an unclosed reasoning block instead of
discarding the text") is **flaky under CI's Xvfb**, and this is established rather
than assumed: commit `45151a3` was executed twice concurrently by CI on separate
runners (a push run and a pull_request run). The e2e job **failed** in one and
**succeeded** in the other, same commit, same code.

Locally the full Playwright suite passes 34/34 at that commit. The test is unrelated
to search v2 and its failure mode is `thinking-body` not appearing within 15 s.

This is the first concrete instance of P2N-FINDING-2 (local-vs-CI Electron
divergence), which was deliberately left unfixed in Phase 2O. It remains unfixed —
recorded, not hidden, and not counted as a pass.

## Note on reading CI for this repository

The e2e job is `continue-on-error: true`, so **a green run conclusion does not mean
every job passed**. At `ad47013` the run showed "success" while the e2e job had
failed. Always inspect the job list, never the run conclusion.
