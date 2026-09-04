import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { openDatabase, migrate, schemaVersion } from '../../electron/store/db';
import { createConversationStore, type ConversationStore } from '../../electron/store/conversations';
import {
  DEFAULT_SEARCH_LIMIT,
  MAX_SEARCH_LIMIT,
  SNIPPET_MATCH_OPEN,
  SNIPPET_MATCH_CLOSE,
} from '../../shared/types';
import {
  seedAppDerivedConversation,
  seedManuallyTitledConversation,
} from '../fixtures/derivedTitleCorpus';

describe('conversation search', () => {
  let db: DatabaseSync;
  let store: ConversationStore;

  beforeEach(() => {
    db = openDatabase(':memory:');
    store = createConversationStore(db);
  });

  describe('migration', () => {
    it('finds conversations that already existed in a v1 database, and bumps user_version to 3 once every migration has run', () => {
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

      // A v1 database migrates through every applied migration, not just the
      // one that existed when this fixture was first written.
      expect(schemaVersion(legacy)).toBe(3);

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

    it('finds pre-existing titles and content after the v2->v3 upgrade, and bumps user_version to 3', () => {
      // Hand-build a v2 database (search over message content exists, titles
      // are not yet indexed) to stand in for a real user's database at the
      // moment of the title-index upgrade. This fixture is deliberately
      // frozen rather than derived from the live MIGRATIONS array: MIGRATIONS
      // is append-only, so what a v2 database looks like on disk today must
      // never change, and building the fixture from MIGRATIONS itself would
      // only prove migrations are consistent with themselves.
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

        CREATE VIRTUAL TABLE messages_fts USING fts5(
          content,
          content='messages',
          content_rowid='rowid',
          tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
          INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;

        CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages BEGIN
          INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
        END;

        CREATE TRIGGER messages_fts_au AFTER UPDATE ON messages BEGIN
          INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
          INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
      `);
      legacy.exec('PRAGMA user_version = 2');

      legacy
        .prepare(
          'INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        )
        .run('conv-2', 'Falcon migration', 'ornith-en', 2000, 2000);
      legacy
        .prepare(
          `INSERT INTO messages (id, conversation_id, seq, role, content, thinking, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run('msg-2', 'conv-2', 0, 'user', 'the kestrel hunts over the meadow at dusk', '', 'complete', 2000);

      expect(schemaVersion(legacy)).toBe(2);

      migrate(legacy);

      expect(schemaVersion(legacy)).toBe(3);

      const migratedTitleStore = createConversationStore(legacy);

      const titleResult = migratedTitleStore.search({ query: 'migration' });
      expect(titleResult.error).toBeUndefined();
      expect(
        titleResult.hits.some((h) => h.conversationId === 'conv-2' && h.matchedIn === 'title'),
      ).toBe(true);

      const contentResult = migratedTitleStore.search({ query: 'kestrel' });
      expect(contentResult.error).toBeUndefined();
      expect(contentResult.hits).toHaveLength(1);
      expect(contentResult.hits[0]).toMatchObject({
        conversationId: 'conv-2',
        messageId: 'msg-2',
        title: 'Falcon migration',
        role: 'user',
        matchedIn: 'content',
      });

      // Rows themselves must survive the migration untouched.
      const conversationRow = legacy
        .prepare('SELECT * FROM conversations WHERE id = ?')
        .get('conv-2') as { title: string; model: string };
      expect(conversationRow.title).toBe('Falcon migration');
      expect(conversationRow.model).toBe('ornith-en');

      const messageRow = legacy.prepare('SELECT * FROM messages WHERE id = ?').get('msg-2') as {
        content: string;
      };
      expect(messageRow.content).toBe('the kestrel hunts over the meadow at dusk');
    });

    it('running migrate() again on an already-migrated database is a no-op, and does not throw', () => {
      // `db`/`store` from the outer beforeEach are already fully migrated.
      // Re-running migrate() must not attempt to re-create conversations_fts
      // or its triggers (there is no IF NOT EXISTS guard, by design -- see
      // db.ts), so this only passes if the version check actually gates it.
      const versionBefore = schemaVersion(db);

      expect(() => migrate(db)).not.toThrow();

      expect(schemaVersion(db)).toBe(versionBefore);

      const c = store.create('ornith-en', 'Idempotence check ptarmigan');
      store.beginTurn(c.id, 'a note about linnets', 'ornith-en');

      expect(store.search({ query: 'Idempotence' }).hits.some((h) => h.matchedIn === 'title')).toBe(
        true,
      );
      expect(store.search({ query: 'linnets' }).hits.some((h) => h.matchedIn === 'content')).toBe(
        true,
      );
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

  describe('title matching', () => {
    it('finds a hit by a term present only in the title, with no messageId or role', () => {
      const c = store.create('ornith-en', 'Nightjar planning session');
      store.beginTurn(c.id, 'an unrelated note about the weather', 'ornith-en');

      const result = store.search({ query: 'nightjar' });
      const titleHit = result.hits.find((h) => h.matchedIn === 'title');

      expect(titleHit).toBeDefined();
      expect(titleHit).toMatchObject({ conversationId: c.id, title: 'Nightjar planning session' });
      expect(titleHit?.messageId).toBeUndefined();
      expect(titleHit?.role).toBeUndefined();
    });

    it('finds a hit by a term present only in message content, with a messageId', () => {
      const c = store.create('ornith-en', 'Generic title');
      const { userMessage } = store.beginTurn(c.id, 'a note about widgeons', 'ornith-en');

      const result = store.search({ query: 'widgeons' });

      expect(result.hits).toHaveLength(1);
      expect(result.hits[0]).toMatchObject({
        matchedIn: 'content',
        messageId: userMessage.id,
        conversationId: c.id,
      });
    });

    // P2P-DEFECT-2: this test previously asserted the defect itself (both a
    // title hit and a content hit for one conversation, from one query).
    // That is a deliberate contract change, not a weakening: the rule is now
    // "a conversation yields a title hit only if no message in it matches
    // the same query" -- content wins, since a content row carries
    // messageId (lets the UI jump to that message) and the title is
    // rendered on every row regardless, so dropping the title row loses
    // nothing.
    it('suppresses the title hit and keeps the content hit when the term appears in both (content wins)', () => {
      const c = store.create('ornith-en', 'Puffin colony notes');
      const { userMessage } = store.beginTurn(
        c.id,
        'the puffin returned to the same burrow this year',
        'ornith-en',
      );

      const result = store.search({ query: 'puffin' });

      expect(result.hits).toHaveLength(1);
      expect(result.hits[0]).toMatchObject({
        matchedIn: 'content',
        conversationId: c.id,
        messageId: userMessage.id,
      });
    });

    it('finds a renamed title and stops finding the old one', () => {
      const c = store.create('ornith-en', 'Original title about egrets');
      store.beginTurn(c.id, 'unrelated content', 'ornith-en');

      expect(store.search({ query: 'egrets' }).hits.some((h) => h.matchedIn === 'title')).toBe(
        true,
      );

      store.rename(c.id, 'Renamed to bitterns');

      expect(store.search({ query: 'egrets' }).hits).toHaveLength(0);
      expect(store.search({ query: 'bitterns' }).hits.some((h) => h.matchedIn === 'title')).toBe(
        true,
      );
    });

    it('stops finding a title hit once its conversation is deleted', () => {
      const c = store.create('ornith-en', 'Conversation about shrikes');
      store.beginTurn(c.id, 'unrelated content', 'ornith-en');
      expect(store.search({ query: 'shrikes' }).hits.some((h) => h.matchedIn === 'title')).toBe(
        true,
      );

      store.remove(c.id);

      expect(store.search({ query: 'shrikes' }).hits).toHaveLength(0);
    });

    it('finds a diacritic title from its plain-ascii spelling', () => {
      const c = store.create('ornith-en', 'Meeting at the café');
      store.beginTurn(c.id, 'unrelated content', 'ornith-en');

      const result = store.search({ query: 'cafe' });

      expect(result.hits.some((h) => h.matchedIn === 'title' && h.conversationId === c.id)).toBe(
        true,
      );
    });

    it("uses the conversation's created_at, not updated_at, as createdAt for a title hit", () => {
      // Mock Date.now so create() and beginTurn() (which bumps updated_at)
      // land on distinct, known timestamps -- otherwise two calls landing in
      // the same millisecond would let a wrongly-sourced createdAt pass by
      // coincidence.
      const dateSpy = vi.spyOn(Date, 'now');
      dateSpy.mockReturnValueOnce(1_000); // store.create(): created_at = updated_at = 1000
      dateSpy.mockReturnValueOnce(2_000); // store.beginTurn(): bumps updated_at to 2000

      const c = store.create('ornith-en', 'Timestamp check wryneck');
      store.beginTurn(c.id, 'unrelated content', 'ornith-en');
      dateSpy.mockRestore();

      const result = store.search({ query: 'wryneck' });
      const titleHit = result.hits.find((h) => h.matchedIn === 'title');

      expect(titleHit).toBeDefined();
      expect(titleHit?.createdAt).toBe(1_000);
      expect(titleHit?.createdAt).not.toBe(2_000);
    });

    // P2P-DEFECT-2: previously one conversation whose title and content both
    // matched (asserting the defect's 2-row shape). After the fix, a single
    // conversation can no longer produce both kinds of hit for the same
    // query (see the suppression test above), so this property -- every
    // hit, of either kind, carries conversationId and title -- is
    // demonstrated across two conversations instead: one matched by title
    // only, one by content only. This is a deliberate contract change, not
    // a weakening of the original assertion.
    it('every hit, of either kind, carries conversationId and title', () => {
      const titleOnly = store.create('ornith-en', 'Dunlin colony notes');
      store.beginTurn(titleOnly.id, 'unrelated content', 'ornith-en');
      const contentOnly = store.create('ornith-en', 'Generic');
      store.beginTurn(contentOnly.id, 'a note about the dunlin', 'ornith-en');

      const result = store.search({ query: 'dunlin' });

      expect(result.hits).toHaveLength(2);
      for (const hit of result.hits) {
        if (hit.conversationId === titleOnly.id) {
          expect(hit.title).toBe('Dunlin colony notes');
        } else {
          expect(hit.conversationId).toBe(contentOnly.id);
          expect(hit.title).toBe('Generic');
        }
      }
    });

    it('reports truncated when combined title and content matches exceed the limit', () => {
      // Two conversations whose titles match, plus two more whose message
      // content matches -- four matches total, combined across both indexes.
      const t1 = store.create('ornith-en', 'Ptarmigan title one');
      store.beginTurn(t1.id, 'unrelated content', 'ornith-en');
      const t2 = store.create('ornith-en', 'Ptarmigan title two');
      store.beginTurn(t2.id, 'unrelated content', 'ornith-en');
      const cnt1 = store.create('ornith-en', 'Generic');
      store.beginTurn(cnt1.id, 'a note about the ptarmigan here', 'ornith-en');
      const cnt2 = store.create('ornith-en', 'Generic');
      store.beginTurn(cnt2.id, 'another note about the ptarmigan there', 'ornith-en');

      const result = store.search({ query: 'ptarmigan', limit: 2 });

      expect(result.hits).toHaveLength(2);
      expect(result.truncated).toBe(true);
    });

    describe('hazardous input in titles', () => {
      const hazardousTitleQueries = [
        '"',
        'a"b',
        'AND',
        'OR',
        'NOT',
        'a-b',
        'a:b',
        'foo(',
        'x AND',
        '*',
      ];

      beforeEach(() => {
        const c = store.create('ornith-en', 'a title about AND, OR, NOT, foo(bar) and a-b:c pairs');
        store.beginTurn(c.id, 'unrelated content', 'ornith-en');
      });

      it.each(hazardousTitleQueries)('does not throw for %j', (query) => {
        expect(() => store.search({ query })).not.toThrow();
      });

      it.each(hazardousTitleQueries)('returns a well-formed result for %j', (query) => {
        const result = store.search({ query });
        expect(Array.isArray(result.hits)).toBe(true);
        expect(typeof result.truncated).toBe('boolean');
      });
    });
  });

  // P2P-DEFECT-2 regression coverage. Every fixture in `title matching`
  // above deliberately isolates the title term from the content term, which
  // is exactly what let the duplicate-row defect ship undetected: in
  // production a conversation's title IS deriveTitle(firstMessage) (see
  // electron/chat/orchestrator.ts), so title and content genuinely overlap.
  // tests/fixtures/derivedTitleCorpus.ts builds conversations that shape.
  describe('title suppression when a message also matches (P2P-DEFECT-2)', () => {
    it('returns only the content hit when the auto-derived title and the first message share the query term', () => {
      const { conversationId, firstMessageId } = seedAppDerivedConversation(
        store,
        'ornith-en',
        'the narwhal migration route this spring',
      );

      const result = store.search({ query: 'narwhal' });

      expect(result.hits).toHaveLength(1);
      expect(result.hits[0]).toMatchObject({
        matchedIn: 'content',
        conversationId,
        messageId: firstMessageId,
      });
    });

    it('still returns exactly one title row for a manually-retitled conversation with no matching message', () => {
      const { conversationId, title } = seedManuallyTitledConversation(
        store,
        'ornith-en',
        'Wombat burrow survey',
        ['notes about soil composition'],
      );

      const result = store.search({ query: 'wombat' });

      expect(result.hits).toHaveLength(1);
      expect(result.hits[0]).toMatchObject({ matchedIn: 'title', conversationId, title });
    });

    it('leaves a content-only match on a later message unaffected', () => {
      // First message deliberately does not mention the term, so the
      // derived title stays clean; the term only appears in a later turn.
      const { conversationId, followUpMessageIds } = seedAppDerivedConversation(
        store,
        'ornith-en',
        'a general check-in about the week',
        ['the platypus sighting was confirmed at the creek'],
      );

      const result = store.search({ query: 'platypus' });

      expect(result.hits).toHaveLength(1);
      expect(result.hits[0]).toMatchObject({
        matchedIn: 'content',
        conversationId,
        messageId: followUpMessageIds[0],
      });
    });

    it('suppresses the title row and returns every matching message as its own content row', () => {
      const { conversationId, firstMessageId, followUpMessageIds } = seedAppDerivedConversation(
        store,
        'ornith-en',
        'the heron count this spring',
        ['another heron was seen at dawn', 'a third heron near the reeds'],
      );

      const result = store.search({ query: 'heron' });

      expect(result.hits).toHaveLength(3);
      expect(result.hits.every((h) => h.matchedIn === 'content')).toBe(true);
      expect(result.hits.some((h) => h.matchedIn === 'title')).toBe(false);
      expect(result.hits.map((h) => h.messageId).sort()).toEqual(
        [firstMessageId, ...followUpMessageIds].sort(),
      );
      for (const hit of result.hits) {
        expect(hit.conversationId).toBe(conversationId);
      }
    });

    it('returns one hit of each kind when the query matches one conversation by title and a different conversation by content', () => {
      const titled = seedManuallyTitledConversation(
        store,
        'ornith-en',
        'Kestrel nesting log',
        ['unrelated notes about the weekly schedule'],
      );
      const contentMatch = seedAppDerivedConversation(
        store,
        'ornith-en',
        'an unrelated first note',
        ['the kestrel returned to the ledge this morning'],
      );

      const result = store.search({ query: 'kestrel' });

      expect(result.hits).toHaveLength(2);
      expect(
        result.hits.some(
          (h) => h.matchedIn === 'title' && h.conversationId === titled.conversationId,
        ),
      ).toBe(true);
      expect(
        result.hits.some(
          (h) => h.matchedIn === 'content' && h.conversationId === contentMatch.conversationId,
        ),
      ).toBe(true);
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
