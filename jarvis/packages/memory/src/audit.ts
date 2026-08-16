import type { ApprovalState, AuditEvent, JarvisEvent, RiskLevel } from '@jarvis/shared';
import { id, now, redact, truncate } from '@jarvis/shared';
import { parseJson, type Db } from './db.ts';

interface AuditRow {
  id: string;
  timestamp: string;
  turn_id: string | null;
  user_id: string;
  agent: string;
  tool: string;
  arguments: string;
  approval_state: AuditEvent['approvalState'];
  approval_id: string | null;
  result: string | null;
  error: string | null;
  duration_ms: number;
  risk: RiskLevel;
}

interface EventRow {
  id: string;
  type: JarvisEvent['type'];
  turn_id: string | null;
  conversation_id: string | null;
  user_id: string;
  agent: string;
  summary: string;
  data: string;
  created_at: string;
}

export interface AuditWriteInput {
  userId: string;
  /** The turn this invocation belongs to. Null only for out-of-turn writes. */
  turnId?: string | null;
  agent: string;
  tool: string;
  arguments: Record<string, unknown>;
  approvalState: ApprovalState | 'not_required';
  approvalId?: string | null;
  result?: string | null;
  error?: string | null;
  durationMs: number;
  risk: RiskLevel;
  conversationId?: string | null;
}

function toAudit(row: AuditRow): AuditEvent {
  return {
    id: row.id,
    timestamp: row.timestamp,
    turnId: row.turn_id ?? null,
    userId: row.user_id,
    agent: row.agent,
    tool: row.tool,
    arguments: parseJson<Record<string, unknown>>(row.arguments, {}),
    approvalState: row.approval_state,
    approvalId: row.approval_id,
    result: row.result,
    error: row.error,
    durationMs: row.duration_ms,
    risk: row.risk,
  };
}

/**
 * The audit log.
 *
 * Every tool invocation lands here — including ones that were denied, expired
 * or failed validation. Arguments and results are redacted before storage so
 * the log can be shown to the user without leaking credentials.
 */
export class AuditRepo {
  constructor(private db: Db) {}

  record(input: AuditWriteInput): AuditEvent {
    const event: AuditEvent = {
      id: id('aud'),
      timestamp: now(),
      turnId: input.turnId ?? null,
      userId: input.userId,
      agent: input.agent,
      tool: input.tool,
      arguments: redact(input.arguments),
      approvalState: input.approvalState,
      approvalId: input.approvalId ?? null,
      result: input.result ? truncate(redact(input.result), 4000) : null,
      error: input.error ? truncate(redact(input.error), 2000) : null,
      durationMs: Math.round(input.durationMs),
      risk: input.risk,
    };

    this.db
      .prepare(
        `INSERT INTO audit_events (id, timestamp, turn_id, user_id, agent, tool, arguments,
           approval_state, approval_id, result, error, duration_ms, risk)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        event.id,
        event.timestamp,
        event.turnId,
        event.userId,
        event.agent,
        event.tool,
        JSON.stringify(event.arguments),
        event.approvalState,
        event.approvalId,
        event.result,
        event.error,
        event.durationMs,
        event.risk,
      );

    // tool_calls mirrors the audit entry with the conversation attached, so the
    // UI can show "what happened in this conversation" without scanning audit.
    this.db
      .prepare(
        `INSERT INTO tool_calls (id, turn_id, conversation_id, user_id, agent, tool, arguments,
           risk, approval_id, state, result, error, duration_ms, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        id('tc'),
        event.turnId,
        input.conversationId ?? null,
        event.userId,
        event.agent,
        event.tool,
        JSON.stringify(event.arguments),
        event.risk,
        event.approvalId,
        event.error ? 'error' : 'ok',
        event.result,
        event.error,
        event.durationMs,
        event.timestamp,
      );

    return event;
  }

  list(userId: string, options: { limit?: number; tool?: string } = {}): AuditEvent[] {
    const limit = options.limit ?? 100;
    const rows = options.tool
      ? (this.db
          .prepare(
            'SELECT * FROM audit_events WHERE user_id = ? AND tool = ? ORDER BY timestamp DESC, rowid DESC LIMIT ?',
          )
          .all(userId, options.tool, limit) as AuditRow[])
      : (this.db
          .prepare(
            'SELECT * FROM audit_events WHERE user_id = ? ORDER BY timestamp DESC, rowid DESC LIMIT ?',
          )
          .all(userId, limit) as AuditRow[]);
    return rows.map(toAudit);
  }

  count(userId: string): number {
    const row = this.db
      .prepare('SELECT COUNT(*) AS n FROM audit_events WHERE user_id = ?')
      .get(userId) as { n: number };
    return row.n;
  }

  /** Every invocation belonging to one turn, oldest first. */
  byTurn(turnId: string): AuditEvent[] {
    const rows = this.db
      .prepare('SELECT * FROM audit_events WHERE turn_id = ? ORDER BY timestamp, rowid')
      .all(turnId) as AuditRow[];
    return rows.map(toAudit);
  }
}

/** Durable copy of the in-memory event bus, so activity survives a restart. */
export class EventRepo {
  constructor(private db: Db) {}

  record(event: JarvisEvent): void {
    this.db
      .prepare(
        `INSERT INTO events (id, type, turn_id, conversation_id, user_id, agent, summary, data, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        event.id,
        event.type,
        event.turnId ?? null,
        event.conversationId,
        event.userId,
        event.agent,
        event.summary,
        JSON.stringify(redact(event.data)),
        event.createdAt,
      );
  }

  list(
    userId: string,
    options: { limit?: number; conversationId?: string; types?: string[] } = {},
  ): JarvisEvent[] {
    const limit = options.limit ?? 100;
    const clauses = ['user_id = ?'];
    const params: unknown[] = [userId];

    if (options.conversationId) {
      clauses.push('conversation_id = ?');
      params.push(options.conversationId);
    }
    if (options.types && options.types.length > 0) {
      clauses.push(`type IN (${options.types.map(() => '?').join(',')})`);
      params.push(...options.types);
    }
    params.push(limit);

    const rows = this.db
      .prepare(
        `SELECT * FROM events WHERE ${clauses.join(' AND ')} ORDER BY created_at DESC, rowid DESC LIMIT ?`,
      )
      .all(...params) as EventRow[];

    return rows.map((row) => ({
      id: row.id,
      type: row.type,
      turnId: row.turn_id ?? null,
      conversationId: row.conversation_id,
      userId: row.user_id,
      agent: row.agent,
      summary: row.summary,
      data: parseJson<Record<string, unknown>>(row.data, {}),
      createdAt: row.created_at,
    }));
  }

  /** Every event belonging to one turn, oldest first. */
  byTurn(turnId: string): JarvisEvent[] {
    const rows = this.db
      .prepare('SELECT * FROM events WHERE turn_id = ? ORDER BY created_at, rowid')
      .all(turnId) as EventRow[];
    return rows.map((row) => ({
      id: row.id,
      type: row.type,
      turnId: row.turn_id ?? null,
      conversationId: row.conversation_id,
      userId: row.user_id,
      agent: row.agent,
      summary: row.summary,
      data: parseJson<Record<string, unknown>>(row.data, {}),
      createdAt: row.created_at,
    }));
  }
}
