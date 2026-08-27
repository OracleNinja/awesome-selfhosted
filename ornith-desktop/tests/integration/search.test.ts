import type { DatabaseSync } from 'node:sqlite';
import { describe, expect, it } from 'vitest';

import { openDatabase } from '../../electron/store/db';
import { createConversationStore, type ConversationStore } from '../../electron/store/conversations';
import { searchConversations, type ConversationSearcher } from '../../electron/search';
import type { SearchResult } from '../../shared/types';

/**
 * Real fixtures, not mocks: an in-memory `node:sqlite` conversation store,
 * the same one `tests/unit/search.test.ts` exercises directly. This file
 * tests `searchConversations` — the IPC-facing handler in `electron/search.ts`
 * — layered on top of that real store, rather than the store's own FTS5
 * behaviour (already covered by `tests/unit/search.test.ts`).
 */
interface Fixtures {
  store: ConversationStore;
}

async function withFixtures(run: (fx: Fixtures) => Promise<void> | void): Promise<void> {
  let db: DatabaseSync | null = null;
  try {
    db = openDatabase(':memory:');
    const store = createConversationStore(db);
    await run({ store });
  } finally {
    db?.close();
  }
}

describe('searchConversations', () => {
  it('returns hits carrying conversation id, title, message id, role, snippet and createdAt', async () => {
    await withFixtures(async ({ store }) => {
      const conv = store.create('ornith-en');
      store.rename(conv.id, 'Nightjar notes');
      const { userMessage } = store.beginTurn(conv.id, 'a note about nightjars at dusk', 'ornith-en');

      const result = searchConversations({ query: 'nightjars' }, { store });

      expect(result.error).toBeUndefined();
      expect(result.hits.length).toBeGreaterThan(0);
      expect(result.hits[0]).toMatchObject({
        conversationId: conv.id,
        title: 'Nightjar notes',
        messageId: userMessage.id,
        role: 'user',
        createdAt: userMessage.createdAt,
      });
      expect(typeof result.hits[0].snippet).toBe('string');
      expect(result.hits[0].snippet.length).toBeGreaterThan(0);
    });
  });

  it('returns zero hits for an empty query on a populated store, not every conversation', async () => {
    await withFixtures(async ({ store }) => {
      // Seeded so "zero hits" is distinguishable from "empty database" -- an
      // empty query returning everything would otherwise look the same as a
      // coincidentally-empty store.
      const a = store.create('ornith-en', 'Alpha');
      store.beginTurn(a.id, 'first conversation content', 'ornith-en');
      const b = store.create('ornith-en', 'Beta');
      store.beginTurn(b.id, 'second conversation content', 'ornith-en');
      const c = store.create('ornith-en', 'Gamma');
      store.beginTurn(c.id, 'third conversation content', 'ornith-en');

      const result = searchConversations({ query: '' }, { store });

      expect(result).toEqual({ hits: [], truncated: false });
    });
  });

  it('reports truncated when more matches exist than the limit', async () => {
    await withFixtures(async ({ store }) => {
      for (let i = 0; i < 5; i += 1) {
        const c = store.create('ornith-en', `Chat ${i}`);
        store.beginTurn(c.id, `a note about wagtails number ${i}`, 'ornith-en');
      }

      const capped = searchConversations({ query: 'wagtails', limit: 3 }, { store });
      expect(capped.hits).toHaveLength(3);
      expect(capped.truncated).toBe(true);

      const uncapped = searchConversations({ query: 'wagtails', limit: 10 }, { store });
      expect(uncapped.hits).toHaveLength(5);
      expect(uncapped.truncated).toBe(false);
    });
  });

  it('returns exactly what the store returned -- no reshaping, no re-sorting', async () => {
    await withFixtures(async ({ store }) => {
      for (let i = 0; i < 4; i += 1) {
        const c = store.create('ornith-en', `Chat ${i}`);
        store.beginTurn(c.id, `a note about kestrels number ${i}`, 'ornith-en');
      }

      const request = { query: 'kestrels', limit: 10 };
      const expected = store.search(request);
      const actual = searchConversations(request, { store });

      expect(actual).toEqual(expected);
    });
  });

  it('returns a safe error, not the raw failure, when the store search throws', () => {
    // The store's own `search` never throws in production (it catches
    // internally -- see tests/unit/search.test.ts), but the handler must not
    // rely on that: it wraps the call so a store that violates the contract
    // still can't leak an error across IPC.
    const throwingStore: ConversationSearcher = {
      search: () => {
        throw new Error(
          'SQLITE_CORRUPT: /Users/dev/ornith/electron/store/conversations.ts:400 near "SELECT": boom',
        );
      },
    };

    const result = searchConversations({ query: 'anything' }, { store: throwingStore });

    expect(result.hits).toEqual([]);
    expect(result.truncated).toBe(false);
    expect(result.error).toBeDefined();
    expect(result.error?.message.length).toBeGreaterThan(0);
    // Safe to show a user: no path, no SQL keyword, no stack fragment.
    expect(result.error?.message).not.toMatch(/select|sqlite|\.ts|\.js|\//i);
  });

  /**
   * The store is seeded here specifically so "zero hits" is a real claim
   * about validation rather than a property of an empty database (the same
   * mistake the "empty query" test above deliberately avoids), and so the
   * inputs that normalize into a *valid* request can be shown to actually
   * match something instead of coincidentally returning nothing.
   */
  async function withPopulatedStore(run: (fx: Fixtures) => Promise<void> | void): Promise<void> {
    await withFixtures(async ({ store }) => {
      const conv = store.create('ornith-en');
      store.rename(conv.id, 'OK notes');
      store.beginTurn(conv.id, 'everything looks ok today', 'ornith-en');
      await run({ store });
    });
  }

  describe.each<[string, unknown]>([
    ['undefined', undefined],
    ['null', null],
    ['a number', 42],
    ['a string', 'a string'],
    ['an empty array', []],
    ['an empty object', {}],
    ['a non-string query', { query: 123 }],
    ['a null query', { query: null }],
  ])('rejected by validation: %s', (_label, input) => {
    it('never throws and returns zero hits on a populated store', async () => {
      await withPopulatedStore(async ({ store }) => {
        let result: SearchResult | undefined;

        expect(() => {
          result = searchConversations(input, { store });
        }).not.toThrow();

        // On a store that genuinely has a matching "ok" message, zero hits
        // proves validation rejected the input before it reached the store
        // -- not merely that the database happened to be empty.
        expect(result).toEqual({ hits: [], truncated: false });
      });
    });
  });

  describe.each<[string, unknown]>([
    ['a non-numeric limit', { query: 'ok', limit: 'not a number' }],
    ['a NaN limit', { query: 'ok', limit: Number.NaN }],
    // Object-literal `__proto__` sets the object's actual prototype (unlike
    // the same key produced by JSON.parse, which creates a plain own
    // property), so a plain `req.query` lookup resolves 'ok' through the
    // prototype chain. This is not a rejected input -- it is a valid
    // request in an unusual disguise -- so it is asserted as one below,
    // rather than asserted (incorrectly) to return zero hits.
    ['an object with a __proto__ key', { __proto__: { query: 'ok' } }],
  ])('valid after normalization: %s', (_label, input) => {
    it('never throws and matches a plain { query: "ok" } request exactly', async () => {
      await withPopulatedStore(async ({ store }) => {
        let result: SearchResult | undefined;

        expect(() => {
          result = searchConversations(input, { store });
        }).not.toThrow();

        const expected = searchConversations({ query: 'ok' }, { store });

        expect(result?.error).toBeUndefined();
        expect(result?.hits.length).toBeGreaterThan(0);
        expect(result).toEqual(expected);
      });
    });
  });
});
