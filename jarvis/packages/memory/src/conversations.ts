import type { Conversation, StoredMessage, ToolCall, User } from '@jarvis/shared';
import { id, now, truncate } from '@jarvis/shared';
import { parseJson, type Db } from './db.ts';

interface ConversationRow {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface MessageRow {
  id: string;
  conversation_id: string;
  role: StoredMessage['role'];
  content: string;
  agent: string | null;
  tool_calls: string | null;
  tool_call_id: string | null;
  created_at: string;
}

function toConversation(row: ConversationRow): Conversation {
  return {
    id: row.id,
    userId: row.user_id,
    title: row.title,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function toMessage(row: MessageRow): StoredMessage {
  const message: StoredMessage = {
    id: row.id,
    conversationId: row.conversation_id,
    role: row.role,
    content: row.content,
    agent: row.agent,
    createdAt: row.created_at,
  };
  const toolCalls = parseJson<ToolCall[] | null>(row.tool_calls, null);
  if (toolCalls && toolCalls.length > 0) message.toolCalls = toolCalls;
  if (row.tool_call_id) message.toolCallId = row.tool_call_id;
  return message;
}

export class UserRepo {
  constructor(private db: Db) {}

  ensure(userId: string, name = 'Operator'): User {
    const existing = this.db.prepare('SELECT * FROM users WHERE id = ?').get(userId) as
      | { id: string; name: string; created_at: string }
      | undefined;
    if (existing) return { id: existing.id, name: existing.name, createdAt: existing.created_at };

    const created = now();
    this.db
      .prepare('INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)')
      .run(userId, name, created);
    return { id: userId, name, createdAt: created };
  }

  get(userId: string): User | null {
    const row = this.db.prepare('SELECT * FROM users WHERE id = ?').get(userId) as
      | { id: string; name: string; created_at: string }
      | undefined;
    return row ? { id: row.id, name: row.name, createdAt: row.created_at } : null;
  }
}

export class ConversationRepo {
  constructor(private db: Db) {}

  create(userId: string, title = 'New conversation'): Conversation {
    const timestamp = now();
    const conversation: Conversation = {
      id: id('conv'),
      userId,
      title,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    this.db
      .prepare(
        'INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
      )
      .run(conversation.id, userId, title, timestamp, timestamp);
    return conversation;
  }

  get(conversationId: string): Conversation | null {
    const row = this.db.prepare('SELECT * FROM conversations WHERE id = ?').get(conversationId) as
      | ConversationRow
      | undefined;
    return row ? toConversation(row) : null;
  }

  list(userId: string, limit = 50): Conversation[] {
    // rowid breaks ties: two conversations created in the same millisecond
    // share an updated_at, and without a tiebreaker their order is arbitrary.
    const rows = this.db
      .prepare(
        'SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?',
      )
      .all(userId, limit) as ConversationRow[];
    return rows.map(toConversation);
  }

  rename(conversationId: string, title: string): void {
    this.db
      .prepare('UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?')
      .run(title, now(), conversationId);
  }

  touch(conversationId: string): void {
    this.db
      .prepare('UPDATE conversations SET updated_at = ? WHERE id = ?')
      .run(now(), conversationId);
  }

  delete(conversationId: string): void {
    this.db.prepare('DELETE FROM conversations WHERE id = ?').run(conversationId);
  }

  /** Give an untitled conversation a title derived from its first user message. */
  autoTitle(conversationId: string, firstMessage: string): void {
    const conversation = this.get(conversationId);
    if (!conversation || conversation.title !== 'New conversation') return;
    const title = truncate(firstMessage.replace(/\s+/g, ' ').trim(), 60) || 'New conversation';
    this.rename(conversationId, title);
  }
}

export class MessageRepo {
  constructor(private db: Db) {}

  append(
    conversationId: string,
    message: Omit<StoredMessage, 'id' | 'conversationId' | 'createdAt' | 'agent'> & {
      agent?: string | null;
    },
  ): StoredMessage {
    const row: StoredMessage = {
      id: id('msg'),
      conversationId,
      role: message.role,
      content: message.content,
      agent: message.agent ?? null,
      createdAt: now(),
    };
    if (message.toolCalls) row.toolCalls = message.toolCalls;
    if (message.toolCallId) row.toolCallId = message.toolCallId;

    this.db
      .prepare(
        `INSERT INTO messages (id, conversation_id, role, content, agent, tool_calls, tool_call_id, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        row.id,
        conversationId,
        row.role,
        row.content,
        row.agent,
        row.toolCalls ? JSON.stringify(row.toolCalls) : null,
        row.toolCallId ?? null,
        row.createdAt,
      );
    return row;
  }

  list(conversationId: string, limit = 200): StoredMessage[] {
    const rows = this.db
      .prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid LIMIT ?')
      .all(conversationId, limit) as MessageRow[];
    return rows.map(toMessage);
  }

  count(conversationId: string): number {
    const row = this.db
      .prepare('SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?')
      .get(conversationId) as { n: number };
    return row.n;
  }
}
