import type { ApprovalRequest, ApprovalState, RiskLevel } from '@jarvis/shared';
import { id, now, secondsFromNow } from '@jarvis/shared';
import { parseJson, type Db } from './db.ts';

interface ApprovalRow {
  id: string;
  user_id: string;
  conversation_id: string | null;
  agent: string;
  tool: string;
  description: string;
  risk: RiskLevel;
  arguments: string;
  state: ApprovalState;
  created_at: string;
  resolved_at: string | null;
  expires_at: string;
  decided_by: string | null;
  note: string | null;
}

function toApproval(row: ApprovalRow): ApprovalRequest {
  return {
    id: row.id,
    userId: row.user_id,
    conversationId: row.conversation_id,
    agent: row.agent,
    tool: row.tool,
    description: row.description,
    risk: row.risk,
    arguments: parseJson<Record<string, unknown>>(row.arguments, {}),
    state: row.state,
    createdAt: row.created_at,
    resolvedAt: row.resolved_at,
    expiresAt: row.expires_at,
    decidedBy: row.decided_by,
    note: row.note,
  };
}

export interface ApprovalCreateInput {
  userId: string;
  conversationId: string | null;
  agent: string;
  tool: string;
  description: string;
  risk: RiskLevel;
  arguments: Record<string, unknown>;
  timeoutSeconds?: number;
}

export class ApprovalRepo {
  constructor(private db: Db) {}

  create(input: ApprovalCreateInput): ApprovalRequest {
    const approval: ApprovalRequest = {
      id: id('apr'),
      userId: input.userId,
      conversationId: input.conversationId,
      agent: input.agent,
      tool: input.tool,
      description: input.description,
      risk: input.risk,
      arguments: input.arguments,
      state: 'pending',
      createdAt: now(),
      resolvedAt: null,
      expiresAt: secondsFromNow(input.timeoutSeconds ?? 900),
      decidedBy: null,
      note: null,
    };
    this.db
      .prepare(
        `INSERT INTO approvals (id, user_id, conversation_id, agent, tool, description, risk,
           arguments, state, created_at, resolved_at, expires_at, decided_by, note)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        approval.id,
        approval.userId,
        approval.conversationId,
        approval.agent,
        approval.tool,
        approval.description,
        approval.risk,
        JSON.stringify(approval.arguments),
        approval.state,
        approval.createdAt,
        approval.resolvedAt,
        approval.expiresAt,
        approval.decidedBy,
        approval.note,
      );
    return approval;
  }

  get(approvalId: string): ApprovalRequest | null {
    this.expireStale();
    const row = this.db.prepare('SELECT * FROM approvals WHERE id = ?').get(approvalId) as
      | ApprovalRow
      | undefined;
    return row ? toApproval(row) : null;
  }

  /**
   * Resolve a pending approval.
   *
   * The update is conditional on `state = 'pending'`, so a decision can only
   * ever be recorded once: a second approve/deny for the same request returns
   * null rather than overwriting the first decision.
   */
  resolve(
    approvalId: string,
    state: Extract<ApprovalState, 'approved' | 'denied'>,
    decidedBy: string,
    note?: string,
  ): ApprovalRequest | null {
    this.expireStale();
    const result = this.db
      .prepare(
        `UPDATE approvals SET state = ?, resolved_at = ?, decided_by = ?, note = ?
         WHERE id = ? AND state = 'pending'`,
      )
      .run(state, now(), decidedBy, note ?? null, approvalId);
    if (result.changes === 0) return null;
    return this.get(approvalId);
  }

  listPending(userId: string, limit = 50): ApprovalRequest[] {
    this.expireStale();
    const rows = this.db
      .prepare(
        `SELECT * FROM approvals WHERE user_id = ? AND state = 'pending' ORDER BY created_at DESC LIMIT ?`,
      )
      .all(userId, limit) as ApprovalRow[];
    return rows.map(toApproval);
  }

  list(userId: string, limit = 100): ApprovalRequest[] {
    this.expireStale();
    const rows = this.db
      .prepare('SELECT * FROM approvals WHERE user_id = ? ORDER BY created_at DESC LIMIT ?')
      .all(userId, limit) as ApprovalRow[];
    return rows.map(toApproval);
  }

  /** Mark timed-out pending approvals as expired. An expired request can never execute. */
  expireStale(reference: string = now()): number {
    return this.db
      .prepare(
        `UPDATE approvals SET state = 'expired', resolved_at = ?
         WHERE state = 'pending' AND expires_at <= ?`,
      )
      .run(reference, reference).changes;
  }
}
