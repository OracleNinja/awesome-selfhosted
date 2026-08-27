import { DatabaseSync } from 'node:sqlite';

/**
 * Ordered migrations. Index + 1 is the resulting PRAGMA user_version, so never
 * reorder or remove an entry — only append.
 */
const MIGRATIONS: Array<(db: DatabaseSync) => void> = [
  function initial(db) {
    db.exec(`
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
  },

  function searchIndex(db) {
    // External-content FTS5 index: the indexed text lives in `messages.content`;
    // this virtual table stores only the inverted index and is kept in sync via
    // the triggers below rather than duplicating the content itself.
    db.exec(`
      CREATE VIRTUAL TABLE messages_fts USING fts5(
        content,
        content='messages',
        content_rowid='rowid',
        tokenize='unicode61 remove_diacritics 2'
      );
    `);

    // Backfill so a user with existing conversations is searchable immediately
    // after the upgrade, not only for messages created from this point on.
    db.exec('INSERT INTO messages_fts(rowid, content) SELECT rowid, content FROM messages;');

    // Keep the index in sync using the FTS5 external-content idiom: deletes and
    // updates are expressed as an explicit 'delete' command against the shadow
    // table before the new state (if any) is inserted.
    db.exec(`
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
  },
];

export function openDatabase(location: string): DatabaseSync {
  const db = new DatabaseSync(location);

  // WAL is what makes a mid-write crash recoverable rather than corrupting.
  // In-memory databases do not support WAL, so skip it there.
  if (location !== ':memory:') {
    db.exec('PRAGMA journal_mode = WAL');
  }
  db.exec('PRAGMA synchronous = NORMAL');
  db.exec('PRAGMA foreign_keys = ON');

  migrate(db);
  return db;
}

export function migrate(db: DatabaseSync): void {
  const row = db.prepare('PRAGMA user_version').get() as { user_version?: number } | undefined;
  const current = Number(row?.user_version ?? 0);

  for (let i = current; i < MIGRATIONS.length; i += 1) {
    db.exec('BEGIN');
    try {
      MIGRATIONS[i](db);
      // PRAGMA does not accept bound parameters; i is our own array index.
      db.exec(`PRAGMA user_version = ${i + 1}`);
      db.exec('COMMIT');
    } catch (err) {
      db.exec('ROLLBACK');
      throw err;
    }
  }
}

export function schemaVersion(db: DatabaseSync): number {
  const row = db.prepare('PRAGMA user_version').get() as { user_version?: number } | undefined;
  return Number(row?.user_version ?? 0);
}
