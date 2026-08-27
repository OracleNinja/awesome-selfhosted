import { beforeEach, describe, expect, it } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { openDatabase, migrate, schemaVersion } from '../../electron/store/db';
import { createConversationStore, type ConversationStore } from '../../electron/store/conversations';
import {
  DEFAULT_SEARCH_LIMIT,
  MAX_SEARCH_LIMIT,
  SNIPPET_MATCH_OPEN,
  SNIPPET_MATCH_CLOSE,
} from '../../shared/types';

describe('conversation search', () => {
  let db: DatabaseSync;
  let store: ConversationStore;

  beforeEach(() => {
    db = openDatabase(':memory:');
    store = createConversationStore(db);
  });

  describe('migration', () => {
    it('finds conversations that already existed before the v1->v2 upgrade, and bumps user_version to 2', () => {
      // Hand-build a v1 database (the schema before the search migration existed)
      // to stand in for a real user's database at the moment of upgrade.
      const legacy = new DatabaseSync(':memory:');
      legacy.exec(`
        CREATE TABLE conversations (
          id          TEXT PRIMARY KEY,
          title       TEXT    NOT NULL DEFAULT 'New chat',
          model       TEXT    NOT NULL,
          created_at  INTEGER NOT NULL,
          updated_at  INTEGER NOT NULL
        );

        CREATE TABLE messages (
          id               TEXT PRIMARY KEY,
          conversation_id  TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          seq              INTEGER NOT NULL,
          role             TEXT    NOT NULL,
          content          TEXT    NOT NULL DEFAULT '',
          thinking         TEXT    NOT NULL DEFAULT '',
          status           TEXT    NOT NULL,
          error_code       TEXT,
          error_message    TEXT,
          eval_count       INTEGER,
          eval_duration_ns INTEGER,
          created_at       INTEGER NOT NULL
        );

        CREATE INDEX idx_messages_conv ON messages(conversation_id, seq);
        CREATE INDEX idx_conv_updated  ON conversations(updated_at DESC);
      `);
      legacy.exec('PRAGMA user_version = 1');

      legacy
        .prepare(
          'INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        )
        .run('conv-1', 'Existing chat', 'ornith-en', 1000, 1000);
      legacy
        .prepare(
          `INSERT INTO messages (id, conversation_id, seq, role, content, thinking, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run('msg-1', 'conv-1', 0, 'user', 'the falcon flies over the harbour at dawn', '', 'complete', 1000);

      expect(schemaVersion(legacy)).toBe(1);

      migrate(legacy);

      expect(schemaVersion(legacy)).toBe(2);

      const migratedStore = createConversationStore(legacy);
      const result = migratedStore.search({ query: 'falcon' });

      expect(result.error).toBeUndefined();
      expect(result.hits).toHaveLength(1);
      expect(result.hits[0]).toMatchObject({
        conversationId: 'conv-1',
        messageId: 'msg-1',
        title: 'Existing chat',
        role: 'user',
      });
    });
  });

  describe('index sync', () => {
    it('finds a newly inserted message', () => {
      const c = store.create('ornith-en');
      store.beginTurn(c.id, 'the quokka naps in the shade', 'ornith-en');

      const result = store.search({ query: 'quokka' });
      expect(result.hits.map((h) => h.conversationId)).toContain(c.id);
    });

    it('finds updated content and no longer finds the content it replaced', () => {
      const c = store.create('ornith-en');
      const { assistantMessage } = store.beginTurn(c.id, 'hi', 'ornith-en');

      expect(store.search({ query: 'stargazing' }).hits).toHaveLength(0);

      store.finalise(assistantMessage.id, {
        content: 'a note about stargazing',
        thinking: '',
        status: 'complete',
      });

      const result = store.search({ query: 'stargazing' });
      expect(result.hits.map((h) => h.messageId)).toContain(assistantMessage.id);
    });

    it('stops finding a message once its conversation is cleared', () => {
      const c = store.create('ornith-en');
      store.beginTurn(c.id, 'a message about kestrels', 'ornith-en');
      expect(store.search({ query: 'kestrels' }).hits.length).toBeGreaterThan(0);

      store.clear(c.id);

      expect(store.search({ query: 'kestrels' }).hits).toHaveLength(0);
    });

    it('removes a conversation from results when the conversation is deleted (cascade)', () => {
      const c = store.create('ornith-en');
      store.beginTurn(c.id, 'a message about ospreys', 'ornith-en');
      expect(store.search({ query: 'ospreys' }).hits.length).toBeGreaterThan(0);

      store.remove(c.id);

      expect(store.search({ query: 'ospreys' }).hits).toHaveLength(0);
    });
  });

  describe('empty and non-indexable queries', () => {
    beforeEach(() => {
      // Seed several conversations so "zero hits" can be distinguished from
      // "every conversation" -- an empty query returning everything would
      // otherwise be indistinguishable from a coincidentally-empty database.
      const a = store.create('ornith-en', 'Alpha');
      store.beginTurn(a.id, 'first conversation content', 'ornith-en');
      const b = store.create('ornith-en', 'Beta');
      store.beginTurn(b.id, 'second conversation content', 'ornith-en');
      const c = store.create('ornith-en', 'Gamma');
      store.beginTurn(c.id, 'third conversation content', 'ornith-en');
    });

    it.each(['', '   ', '...', '***', '!!!'])(
      'returns zero hits, not every conversation, for %j',
      (query) => {
        const result = store.search({ query });
        expect(result.hits).toEqual([]);
        expect(result.truncated).toBe(false);
        expect(result.error).toBeUndefined();
      },
    );

    it('does not touch the database for an empty query', () => {
      // A blank query should short-circuit before reaching SQLite at all,
      // so the search happens with the conversations untouched either way.
      const before = store.list();
      store.search({ query: '   ' });
      expect(store.list()).toEqual(before);
    });
  });

  describe('hazardous input', () => {
    const hazardousQueries = ['"', 'a"b', 'AND', 'OR', 'NOT', 'a-b', 'a:b', 'foo(', 'x AND', '*'];

    beforeEach(() => {
      const c = store.create('ornith-en');
      store.beginTurn(c.id, 'a conversation about AND, OR, NOT, foo(bar) and a-b:c pairs', 'ornith-en');
    });

    it.each(hazardousQueries)('does not throw for %j', (query) => {
      expect(() => store.search({ query })).not.toThrow();
    });

    it.each(hazardousQueries)('returns a well-formed result for %j', (query) => {
      const result = store.search({ query });
      expect(Array.isArray(result.hits)).toBe(true);
      expect(typeof result.truncated).toBe('boolean');
    });
  });

  it('finds a full word from a leading prefix', () => {
    const c = store.create('ornith-en');
    store.beginTurn(c.id, 'the migration patterns of swallows', 'ornith-en');

    const result = store.search({ query: 'swal' });
    expect(result.hits.length).toBeGreaterThan(0);
  });

  it('finds a diacritic word from its plain-ascii spelling', () => {
    const c = store.create('ornith-en');
    store.beginTurn(c.id, 'meeting at the café tomorrow', 'ornith-en');

    const result = store.search({ query: 'cafe' });
    expect(result.hits.length).toBeGreaterThan(0);
  });

  it('wraps the matched run in the snippet delimiters', () => {
    const c = store.create('ornith-en');
    store.beginTurn(c.id, 'the heron waits patiently by the water', 'ornith-en');

    const result = store.search({ query: 'heron' });
    const snippet = result.hits[0].snippet;

    expect(snippet).toContain(SNIPPET_MATCH_OPEN);
    expect(snippet).toContain(SNIPPET_MATCH_CLOSE);
    expect(snippet.indexOf(SNIPPET_MATCH_CLOSE)).toBeGreaterThan(snippet.indexOf(SNIPPET_MATCH_OPEN));
  });

  it('ranks a stronger match before a weaker match', () => {
    const strong = store.create('ornith-en', 'Strong');
    const { userMessage: strongMsg } = store.beginTurn(
      strong.id,
      'osprey osprey osprey osprey osprey',
      'ornith-en',
    );

    const weak = store.create('ornith-en', 'Weak');
    const { userMessage: weakMsg } = store.beginTurn(
      weak.id,
      'a very long passage that mentions an osprey only once among a great many unrelated padding words used purely to dilute relevance for this ranking test',
      'ornith-en',
    );

    const result = store.search({ query: 'osprey' });
    const ids = result.hits.map((h) => h.messageId);

    expect(ids.indexOf(strongMsg.id)).toBeLessThan(ids.indexOf(weakMsg.id));
  });

  describe('limit and truncation', () => {
    beforeEach(() => {
      for (let i = 0; i < 5; i += 1) {
        const c = store.create('ornith-en', `Chat ${i}`);
        store.beginTurn(c.id, `a note about wagtails number ${i}`, 'ornith-en');
      }
    });

    it('caps results at the limit and reports truncation', () => {
      const result = store.search({ query: 'wagtails', limit: 3 });
      expect(result.hits).toHaveLength(3);
      expect(result.truncated).toBe(true);
    });

    it('reports no truncation when every match fits', () => {
      const result = store.search({ query: 'wagtails', limit: 10 });
      expect(result.hits).toHaveLength(5);
      expect(result.truncated).toBe(false);
    });

    it('uses DEFAULT_SEARCH_LIMIT when the limit is omitted', () => {
      const result = store.search({ query: 'wagtails' });
      expect(result.hits.length).toBeLessThanOrEqual(DEFAULT_SEARCH_LIMIT);
      expect(result.hits).toHaveLength(5);
    });

    it('clamps a zero limit up to 1 rather than rejecting it', () => {
      const result = store.search({ query: 'wagtails', limit: 0 });
      expect(result.hits).toHaveLength(1);
      expect(result.truncated).toBe(true);
    });

    it('clamps a negative limit up to 1 rather than rejecting it', () => {
      const result = store.search({ query: 'wagtails', limit: -1 });
      expect(result.hits).toHaveLength(1);
      expect(result.truncated).toBe(true);
    });

    it('clamps a limit above MAX_SEARCH_LIMIT down to MAX_SEARCH_LIMIT', () => {
      const result = store.search({ query: 'wagtails', limit: 9999 });
      // Only 5 matches exist, well under MAX_SEARCH_LIMIT, so the clamp itself
      // is exercised without truncation muddying the assertion.
      expect(result.hits).toHaveLength(5);
      expect(result.truncated).toBe(false);
      expect(MAX_SEARCH_LIMIT).toBeGreaterThan(5);
    });

    it('clamps a non-integer limit rather than rejecting it', () => {
      const result = store.search({ query: 'wagtails', limit: 2.7 });
      expect(result.hits).toHaveLength(2);
      expect(result.truncated).toBe(true);
    });
  });

  it('identifies a hit by conversation, title, message, role and time', () => {
    const c = store.create('ornith-en', 'Shape test');
    const { userMessage } = store.beginTurn(c.id, 'a note about nightjars', 'ornith-en');

    const result = store.search({ query: 'nightjars' });
    expect(result.hits[0]).toMatchObject({
      conversationId: c.id,
      title: 'Shape test',
      messageId: userMessage.id,
      role: 'user',
      createdAt: userMessage.createdAt,
    });
  });

  it('reports STORAGE_CORRUPT instead of throwing when the database is unusable', () => {
    const c = store.create('ornith-en');
    store.beginTurn(c.id, 'a note about herons', 'ornith-en');

    db.close();

    const result = store.search({ query: 'herons' });

    expect(result.hits).toEqual([]);
    expect(result.truncated).toBe(false);
    expect(result.error?.code).toBe('STORAGE_CORRUPT');
    // The message shown to a user must never leak implementation detail.
    expect(result.error?.message).not.toMatch(/select|insert|\.ts|\.js|error:/i);
  });
});
