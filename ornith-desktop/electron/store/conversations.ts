import type { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import type {
  AppError,
  ChatMessage,
  Conversation,
  ConversationSummary,
  GenerationStats,
  MessageStatus,
  Role,
} from '../../shared/types';

interface MessageRow {
  id: string;
  conversation_id: string;
  seq: number;
  role: string;
  content: string;
  thinking: string;
  status: string;
  error_code: string | null;
  error_message: string | null;
  eval_count: number | null;
  eval_duration_ns: number | null;
  created_at: number;
}

interface ConversationRow {
  id: string;
  title: string;
  model: string;
  created_at: number;
  updated_at: number;
}

function toMessage(row: MessageRow): ChatMessage {
  const message: ChatMessage = {
    id: row.id,
    seq: Number(row.seq),
    role: row.role as Role,
    content: row.content,
    thinking: row.thinking,
    status: row.status as MessageStatus,
    createdAt: Number(row.created_at),
  };

  if (row.error_code) {
    message.error = {
      code: row.error_code as AppError['code'],
      message: row.error_message ?? 'Something went wrong.',
    };
  }

  if (row.eval_count !== null && row.eval_duration_ns !== null) {
    const seconds = Number(row.eval_duration_ns) / 1e9;
    message.stats = {
      evalCount: Number(row.eval_count),
      evalDurationNs: Number(row.eval_duration_ns),
      promptEvalCount: 0,
      totalDurationNs: 0,
      tokensPerSecond: seconds > 0 ? Number(row.eval_count) / seconds : 0,
    };
  }

  return message;
}

export interface AppendedTurn {
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
}

export interface ConversationStore {
  create(model: string, title?: string): Conversation;
  list(): ConversationSummary[];
  get(id: string): Conversation | null;
  rename(id: string, title: string): void;
  remove(id: string): void;
  clear(id: string): void;
  exists(id: string): boolean;
  /** Inserts the user turn and the assistant placeholder in one transaction. */
  beginTurn(conversationId: string, userText: string, model: string): AppendedTurn;
  updateStreaming(messageId: string, content: string, thinking: string): void;
  finalise(
    messageId: string,
    update: {
      content: string;
      thinking: string;
      status: MessageStatus;
      error?: AppError;
      stats?: GenerationStats;
    },
  ): void;
  /** Rewrites rows left mid-stream by a crash. Returns how many were repaired. */
  recoverInterrupted(): number;
}

export function createConversationStore(db: DatabaseSync): ConversationStore {
  const touch = db.prepare('UPDATE conversations SET updated_at = ? WHERE id = ?');

  function touchConversation(id: string, when = Date.now()): void {
    touch.run(when, id);
  }

  return {
    create(model, title = 'New chat') {
      const now = Date.now();
      const id = randomUUID();
      db.prepare(
        'INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
      ).run(id, title, model, now, now);
      return { id, title, model, createdAt: now, updatedAt: now, messages: [] };
    },

    list() {
      const rows = db
        .prepare(
          `SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
           FROM conversations c ORDER BY c.updated_at DESC`,
        )
        .all() as unknown as Array<ConversationRow & { message_count: number }>;

      return rows.map((r) => ({
        id: r.id,
        title: r.title,
        model: r.model,
        createdAt: Number(r.created_at),
        updatedAt: Number(r.updated_at),
        messageCount: Number(r.message_count),
      }));
    },

    get(id) {
      const row = db.prepare('SELECT * FROM conversations WHERE id = ?').get(id) as
        | ConversationRow
        | undefined;
      if (!row) return null;

      const messages = (
        db
          .prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC')
          .all(id) as unknown as MessageRow[]
      ).map(toMessage);

      return {
        id: row.id,
        title: row.title,
        model: row.model,
        createdAt: Number(row.created_at),
        updatedAt: Number(row.updated_at),
        messages,
      };
    },

    exists(id) {
      return db.prepare('SELECT 1 FROM conversations WHERE id = ?').get(id) !== undefined;
    },

    rename(id, title) {
      db.prepare('UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?').run(
        title.trim() || 'New chat',
        Date.now(),
        id,
      );
    },

    remove(id) {
      // ON DELETE CASCADE removes the messages, given PRAGMA foreign_keys = ON.
      db.prepare('DELETE FROM conversations WHERE id = ?').run(id);
    },

    clear(id) {
      db.exec('BEGIN');
      try {
        db.prepare('DELETE FROM messages WHERE conversation_id = ?').run(id);
        db.prepare('UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?').run(
          'New chat',
          Date.now(),
          id,
        );
        db.exec('COMMIT');
      } catch (err) {
        db.exec('ROLLBACK');
        throw err;
      }
    },

    beginTurn(conversationId, userText, model) {
      const now = Date.now();
      const seqRow = db
        .prepare('SELECT COALESCE(MAX(seq), -1) AS max_seq FROM messages WHERE conversation_id = ?')
        .get(conversationId) as { max_seq: number };
      const nextSeq = Number(seqRow.max_seq) + 1;

      const userMessage: ChatMessage = {
        id: randomUUID(),
        seq: nextSeq,
        role: 'user',
        content: userText,
        thinking: '',
        status: 'complete',
        createdAt: now,
      };
      const assistantMessage: ChatMessage = {
        id: randomUUID(),
        seq: nextSeq + 1,
        role: 'assistant',
        content: '',
        thinking: '',
        status: 'streaming',
        createdAt: now + 1,
      };

      const insert = db.prepare(
        `INSERT INTO messages (id, conversation_id, seq, role, content, thinking, status, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      );

      // One transaction: the user's message is never persisted without its
      // placeholder, so a crash can always be reasoned about on restart.
      db.exec('BEGIN');
      try {
        insert.run(
          userMessage.id,
          conversationId,
          userMessage.seq,
          'user',
          userText,
          '',
          'complete',
          now,
        );
        insert.run(
          assistantMessage.id,
          conversationId,
          assistantMessage.seq,
          'assistant',
          '',
          '',
          'streaming',
          now + 1,
        );
        db.prepare('UPDATE conversations SET updated_at = ?, model = ? WHERE id = ?').run(
          now,
          model,
          conversationId,
        );
        db.exec('COMMIT');
      } catch (err) {
        db.exec('ROLLBACK');
        throw err;
      }

      return { userMessage, assistantMessage };
    },

    updateStreaming(messageId, content, thinking) {
      db.prepare('UPDATE messages SET content = ?, thinking = ? WHERE id = ?').run(
        content,
        thinking,
        messageId,
      );
    },

    finalise(messageId, update) {
      db.exec('BEGIN');
      try {
        db.prepare(
          `UPDATE messages
             SET content = ?, thinking = ?, status = ?,
                 error_code = ?, error_message = ?,
                 eval_count = ?, eval_duration_ns = ?
           WHERE id = ?`,
        ).run(
          update.content,
          update.thinking,
          update.status,
          update.error?.code ?? null,
          update.error?.message ?? null,
          update.stats?.evalCount ?? null,
          update.stats?.evalDurationNs ?? null,
          messageId,
        );

        const row = db
          .prepare('SELECT conversation_id FROM messages WHERE id = ?')
          .get(messageId) as { conversation_id?: string } | undefined;
        if (row?.conversation_id) touchConversation(row.conversation_id);

        db.exec('COMMIT');
      } catch (err) {
        db.exec('ROLLBACK');
        throw err;
      }
    },

    recoverInterrupted() {
      // A row still marked 'streaming' at startup means the app died mid-answer.
      // Keep the partial text; relabel it so the UI shows it as interrupted.
      const result = db
        .prepare(
          `UPDATE messages
              SET status = 'cancelled',
                  error_code = 'STREAM_INTERRUPTED',
                  error_message = 'This response was interrupted when the app closed.'
            WHERE status = 'streaming'`,
        )
        .run();
      return Number(result.changes ?? 0);
    },
  };
}
