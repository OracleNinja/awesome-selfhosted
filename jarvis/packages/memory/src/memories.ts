import type { Memory, MemorySearchResult, MemoryType } from '@jarvis/shared';
import { id, now, tokenize } from '@jarvis/shared';
import { parseJson, type Db } from './db.ts';

interface MemoryRow {
  id: string;
  user_id: string;
  type: MemoryType;
  content: string;
  source: string;
  confidence: number;
  importance: number;
  tags: string;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
}

function toMemory(row: MemoryRow): Memory {
  return {
    id: row.id,
    userId: row.user_id,
    type: row.type,
    content: row.content,
    source: row.source,
    confidence: row.confidence,
    importance: row.importance,
    tags: parseJson<string[]>(row.tags, []),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    expiresAt: row.expires_at,
  };
}

export interface MemoryWriteInput {
  userId: string;
  type: MemoryType;
  content: string;
  source: string;
  confidence?: number;
  importance?: number;
  tags?: string[];
  expiresAt?: string | null;
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

export class MemoryRepo {
  constructor(private db: Db) {}

  /**
   * Write a memory. Memory writes are explicit — nothing calls this
   * automatically for every chat message.
   *
   * Re-writing identical content for the same user+type updates the existing
   * row (raising confidence/importance to the higher of the two) rather than
   * creating duplicates.
   */
  write(input: MemoryWriteInput): Memory {
    const timestamp = now();
    const content = input.content.trim();
    if (!content) throw new Error('memory content must not be empty');

    const existing = this.db
      .prepare('SELECT * FROM memories WHERE user_id = ? AND type = ? AND content = ?')
      .get(input.userId, input.type, content) as MemoryRow | undefined;

    if (existing) {
      const confidence = Math.max(existing.confidence, clamp01(input.confidence ?? 0.8));
      const importance = Math.max(existing.importance, clamp01(input.importance ?? 0.5));
      const tags = Array.from(
        new Set([...parseJson<string[]>(existing.tags, []), ...(input.tags ?? [])]),
      );
      this.db
        .prepare(
          `UPDATE memories SET confidence = ?, importance = ?, tags = ?, source = ?,
             updated_at = ?, expires_at = ? WHERE id = ?`,
        )
        .run(
          confidence,
          importance,
          JSON.stringify(tags),
          input.source,
          timestamp,
          input.expiresAt ?? existing.expires_at,
          existing.id,
        );
      return this.get(existing.id)!;
    }

    const memory: Memory = {
      id: id('mem'),
      userId: input.userId,
      type: input.type,
      content,
      source: input.source,
      confidence: clamp01(input.confidence ?? 0.8),
      importance: clamp01(input.importance ?? 0.5),
      tags: input.tags ?? [],
      createdAt: timestamp,
      updatedAt: timestamp,
      expiresAt: input.expiresAt ?? null,
    };

    this.db
      .prepare(
        `INSERT INTO memories (id, user_id, type, content, source, confidence, importance, tags,
           created_at, updated_at, expires_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        memory.id,
        memory.userId,
        memory.type,
        memory.content,
        memory.source,
        memory.confidence,
        memory.importance,
        JSON.stringify(memory.tags),
        memory.createdAt,
        memory.updatedAt,
        memory.expiresAt,
      );
    return memory;
  }

  get(memoryId: string): Memory | null {
    const row = this.db.prepare('SELECT * FROM memories WHERE id = ?').get(memoryId) as
      | MemoryRow
      | undefined;
    return row ? toMemory(row) : null;
  }

  delete(memoryId: string): boolean {
    return this.db.prepare('DELETE FROM memories WHERE id = ?').run(memoryId).changes > 0;
  }

  /** Remove expired `temporary` memories. Called on read paths and on startup. */
  pruneExpired(reference: string = now()): number {
    return this.db
      .prepare('DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?')
      .run(reference).changes;
  }

  list(userId: string, options: { type?: MemoryType; limit?: number } = {}): Memory[] {
    this.pruneExpired();
    const limit = options.limit ?? 100;
    const rows = options.type
      ? (this.db
          .prepare(
            'SELECT * FROM memories WHERE user_id = ? AND type = ? ORDER BY importance DESC, updated_at DESC LIMIT ?',
          )
          .all(userId, options.type, limit) as MemoryRow[])
      : (this.db
          .prepare(
            'SELECT * FROM memories WHERE user_id = ? ORDER BY importance DESC, updated_at DESC LIMIT ?',
          )
          .all(userId, limit) as MemoryRow[]);
    return rows.map(toMemory);
  }

  /**
   * Lexical relevance search.
   *
   * v0.1 scores token overlap, weighted by importance, confidence and
   * recency. The interface is deliberately the same shape an embedding-backed
   * implementation would use, so v0.2 can swap the scorer without touching
   * callers.
   */
  search(
    userId: string,
    query: string,
    options: { limit?: number; type?: MemoryType; minScore?: number } = {},
  ): MemorySearchResult[] {
    this.pruneExpired();
    const limit = options.limit ?? 8;
    const minScore = options.minScore ?? 0.01;
    const queryTokens = new Set(tokenize(query));

    const candidates = this.list(userId, { type: options.type, limit: 1000 });
    if (queryTokens.size === 0) {
      return candidates.slice(0, limit).map((memory) => ({ ...memory, score: memory.importance }));
    }

    const nowMs = Date.now();
    const scored = candidates.map((memory) => {
      const haystack = new Set([...tokenize(memory.content), ...memory.tags.map((t) => t.toLowerCase())]);
      let overlap = 0;
      for (const queryToken of queryTokens) {
        if (haystack.has(queryToken)) overlap += 1;
        else if ([...haystack].some((word) => word.includes(queryToken) || queryToken.includes(word))) {
          overlap += 0.5;
        }
      }
      const lexical = overlap / queryTokens.size;
      const ageDays = (nowMs - Date.parse(memory.updatedAt)) / 86_400_000;
      const recency = 1 / (1 + Math.max(0, ageDays) / 30);
      const score =
        lexical * 0.65 + memory.importance * 0.2 + memory.confidence * 0.1 + recency * 0.05;
      return { ...memory, score: lexical === 0 ? 0 : Number(score.toFixed(4)) };
    });

    return scored
      .filter((memory) => memory.score >= minScore)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  count(userId: string): number {
    const row = this.db
      .prepare('SELECT COUNT(*) AS n FROM memories WHERE user_id = ?')
      .get(userId) as { n: number };
    return row.n;
  }
}
