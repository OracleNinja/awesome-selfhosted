/**
 * Schema migrations.
 *
 * Migrations are append-only: never edit an existing entry, add a new one.
 * `user_version` in the SQLite file tracks which have been applied.
 */
export interface Migration {
  version: number;
  name: string;
  sql: string;
}

export const MIGRATIONS: Migration[] = [
  {
    version: 1,
    name: 'initial_schema',
    sql: /* sql */ `
      CREATE TABLE users (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  TEXT NOT NULL
      );

      CREATE TABLE conversations (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
      );
      CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);

      CREATE TABLE messages (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL CHECK (role IN ('system','user','assistant','tool')),
        content         TEXT NOT NULL,
        agent           TEXT,
        tool_calls      TEXT,
        tool_call_id    TEXT,
        created_at      TEXT NOT NULL
      );
      CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

      CREATE TABLE memories (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type        TEXT NOT NULL CHECK (type IN
                      ('preference','fact','goal','project','person','instruction','temporary')),
        content     TEXT NOT NULL,
        source      TEXT NOT NULL,
        confidence  REAL NOT NULL DEFAULT 0.8,
        importance  REAL NOT NULL DEFAULT 0.5,
        tags        TEXT NOT NULL DEFAULT '[]',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        expires_at  TEXT
      );
      CREATE INDEX idx_memories_user ON memories(user_id, importance DESC);
      CREATE UNIQUE INDEX idx_memories_dedupe ON memories(user_id, type, content);

      CREATE TABLE tasks (
        id             TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title          TEXT NOT NULL,
        detail         TEXT NOT NULL DEFAULT '',
        status         TEXT NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open','in_progress','blocked','done','cancelled')),
        priority       TEXT NOT NULL DEFAULT 'normal'
                         CHECK (priority IN ('low','normal','high','urgent')),
        assigned_agent TEXT,
        due_at         TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
      );
      CREATE INDEX idx_tasks_user ON tasks(user_id, status, created_at DESC);

      CREATE TABLE agents (
        name        TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        purpose     TEXT NOT NULL,
        max_risk    TEXT NOT NULL,
        read_only   INTEGER NOT NULL,
        allowed_tools TEXT NOT NULL,
        run_count   INTEGER NOT NULL DEFAULT 0,
        last_run_at TEXT,
        created_at  TEXT NOT NULL
      );

      CREATE TABLE tool_calls (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT,
        user_id         TEXT NOT NULL,
        agent           TEXT NOT NULL,
        tool            TEXT NOT NULL,
        arguments       TEXT NOT NULL,
        risk            TEXT NOT NULL,
        approval_id     TEXT,
        state           TEXT NOT NULL,
        result          TEXT,
        error           TEXT,
        duration_ms     INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL
      );
      CREATE INDEX idx_tool_calls_user ON tool_calls(user_id, created_at DESC);

      CREATE TABLE approvals (
        id              TEXT PRIMARY KEY,
        user_id         TEXT NOT NULL,
        conversation_id TEXT,
        agent           TEXT NOT NULL,
        tool            TEXT NOT NULL,
        description     TEXT NOT NULL,
        risk            TEXT NOT NULL,
        arguments       TEXT NOT NULL,
        state           TEXT NOT NULL CHECK (state IN ('pending','approved','denied','expired')),
        created_at      TEXT NOT NULL,
        resolved_at     TEXT,
        expires_at      TEXT NOT NULL,
        decided_by      TEXT,
        note            TEXT
      );
      CREATE INDEX idx_approvals_state ON approvals(user_id, state, created_at DESC);

      CREATE TABLE audit_events (
        id             TEXT PRIMARY KEY,
        timestamp      TEXT NOT NULL,
        user_id        TEXT NOT NULL,
        agent          TEXT NOT NULL,
        tool           TEXT NOT NULL,
        arguments      TEXT NOT NULL,
        approval_state TEXT NOT NULL,
        approval_id    TEXT,
        result         TEXT,
        error          TEXT,
        duration_ms    INTEGER NOT NULL DEFAULT 0,
        risk           TEXT NOT NULL
      );
      CREATE INDEX idx_audit_time ON audit_events(user_id, timestamp DESC);

      CREATE TABLE events (
        id              TEXT PRIMARY KEY,
        type            TEXT NOT NULL,
        conversation_id TEXT,
        user_id         TEXT NOT NULL,
        agent           TEXT NOT NULL,
        summary         TEXT NOT NULL,
        data            TEXT NOT NULL,
        created_at      TEXT NOT NULL
      );
      CREATE INDEX idx_events_time ON events(user_id, created_at DESC);
    `,
  },
];
