# Ornith Desktop — search correctness and e2e reliability

Phase 2Q. Two defects fixed, one of them misdiagnosed first, and the fixture habit that
hid both.

## P2P-DEFECT-2 — one conversation, two search rows (fixed)

`electron/chat/orchestrator.ts:89` sets a new conversation's title to
`deriveTitle(userText)` — the title **is** the first user message. So a query matching a
term in that opening message matched both FTS5 indexes for the same conversation, and the
union emitted a content row *and* a title row for it. Not an edge case: the common one.

**The rule now implemented:** a conversation yields a title hit only if no message in it
matches the same query.

Two properties, both deliberate:

- **Page-independent.** The suppression subquery asks "does any message in this
  conversation match", never "does one on this page". Which page is being fetched can
  never change whether a title hit is suppressed.
- **Content wins.** Content rows carry `messageId`, which is what lets the UI jump to a
  specific message; the title renders on every row regardless. Dropping the title row
  loses nothing, dropping content rows would lose message navigation.

Measured before implementing, on the in-repo 540-conversation corpus: defect case 2 rows
-> 1; title-only match still returns its title row; cost +0.2 to +0.5 ms (p50 3.32 ->
3.77 ms), far inside the 200 ms backstop; query plan still serves both FTS virtual tables
through a MATCH index with no base-table scan, SQLite adding a bloom filter for the
`NOT IN`.

### Two existing tests changed, and why that is not a weakening

- `tests/unit/search.test.ts` `'finds both a title hit and a content hit when the term
  appears in both'` asserted the defect's own shape as intended behaviour. Now asserts one
  content row.
- `'every hit, of either kind, carries conversationId and title'` built ONE conversation
  matching both ways and asserted `toHaveLength(2)`. Split into two conversations — one
  title-only, one content-only — so it still yields two hits of two kinds and still proves
  what its name claims. Changing the count to 1 would have kept it green while gutting it.

## P2Q-DEFECT-1 — an incomplete reasoning block was hidden, not shown (fixed)

Recorded in 2P as a flaky test, because the same commit passed in one CI execution and
failed in another. That observation was right and **the conclusion drawn from it was
wrong**. The test was deterministic; the product was timing-dependent.

`src/components/ThinkingPanel.tsx` did `useState(defaultOpen || incomplete)`. `useState`
captures its argument on the first render only. `incomplete` is `STREAM_MALFORMED`, which
`orchestrator.ts:172-184` sets at **finalise** — after the panel has already mounted with
the streaming reasoning text, when `incomplete` is still false and `showThinkingByDefault`
defaults to false. The label recomputed every render, so it correctly said "Incomplete
reasoning"; `open` stayed stale at false, so the preserved text was never rendered.

The test passed only when the reasoning text and the finalise landed in the same React
render batch. A fast machine usually batches them; a loaded CI runner usually does not.

**What that meant for users, not just for CI:** the flag exists so an unclosed reasoning
block keeps its text instead of discarding it. Under load, a real user saw a collapsed
"Incomplete reasoning" toggle with the text hidden behind it.

**The fix** derives openness from props instead of storing it:

    const [userOverride, setUserOverride] = useState<boolean | null>(null);
    const autoOpen = incomplete || defaultOpen;
    const open = userOverride ?? autoOpen;

This removes the staleness class rather than patching the one instance. The tri-state
`userOverride` distinguishes "the user has an opinion" from "the user chose false", which
the previous boolean could not.

A first attempt used `incomplete || (defaultOpen && !answerStarted)`. Measured: that
silently broke "Always expand reasoning", collapsing the panel the moment the answer
arrived — and **no test in the suite covered that path**, so nothing would have caught it.
A regression test for it now exists.

The renderer was swept for the same bug class: `ThinkingPanel` was the only `useState`
whose initializer read props. Noted but not acted on — `eslint.config.js` registers no
`react-hooks` plugin, so `exhaustive-deps` is unenforced. It would not have caught this
one (a stale initializer is not a missing dependency), but it is a gap.

## The habit that hid both

Every unit and integration fixture deliberately isolated the title term from the content
term — `Quokka retrospective` with body `an unrelated note about wetlands`. Good discipline
for testing two indexes independently, and exactly what guaranteed nobody tested the shape
the product actually produces. 320 tests passed over the duplicate-row defect.

`tests/fixtures/derivedTitleCorpus.ts` now seeds both real shapes: `seedAppDerivedConversation`
mirrors the orchestrator (title derived from the first message, so title and content
overlap) and `seedManuallyTitledConversation` mirrors the real `conv:create` -> `conv:rename`
sequence — the only way a title-only match can exist, since a derived title is always a
substring of its own first message.

## Fixture hygiene, learned the hard way

The "frozen" `populated-v2.db` migration fixture from 2P was found **already at
`user_version = 3`**: something had opened it in place, and `openDatabase` migrates on
open. Any migration test using it would have passed for the wrong reason, testing 3 -> 3.

A migration fixture must be regenerated from the hand-written historical schema on every
run, never trusted as a stored file. The generator exists precisely because the fixture has
to be a historical fact rather than something derived from the live migrations array; that
reasoning extends to not trusting a saved copy of it either.

## Known gap, recorded not hidden

No test exercises whether title suppression stays correct once the outer `LIMIT` is
actually forcing truncation across overlapping title and content matches. It is true by
construction — suppression happens in `WHERE`, before `LIMIT` — and the worker who found
the gap could not construct a test that would meaningfully fail without it. Verified by a
Lead probe across limits 1, 2, 3 and 50; not committed as a test.
