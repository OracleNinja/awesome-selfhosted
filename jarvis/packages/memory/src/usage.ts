/**
 * Model usage.
 *
 * Providers already measured tokens and latency on every call; the runtime
 * threw both away. This records them, correlated to the turn that caused them.
 *
 * Two rules:
 *  - Nothing here is invented. A provider that does not report token counts
 *    produces nulls, not zeroes — "unknown" and "free" are different facts.
 *  - No pricing. Token counts are authoritative and provider-neutral; dollar
 *    figures need a price table that would have to be maintained and would go
 *    stale silently. Cost belongs on top of this, later, not inside it.
 */
import type { ISODate } from '@jarvis/shared';
import { id, now, truncate } from '@jarvis/shared';
import type { Db } from './db.ts';

export type UsageOutcome = 'ok' | 'error' | 'cancelled';

export interface ModelUsageRecord {
  id: string;
  turnId: string | null;
  timestamp: ISODate;
  userId: string;
  agent: string;
  provider: string;
  model: string;
  /** Capabilities the caller asked the router for. */
  requested: string[];
  fallbackUsed: boolean;
  routingReason: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  latencyMs: number;
  outcome: UsageOutcome;
  error: string | null;
}

export interface ModelUsageInput {
  turnId?: string | null;
  userId: string;
  agent: string;
  provider: string;
  model: string;
  requested?: string[];
  fallbackUsed?: boolean;
  routingReason?: string | null;
  inputTokens?: number | null;
  outputTokens?: number | null;
  latencyMs: number;
  outcome: UsageOutcome;
  error?: string | null;
}

interface UsageRow {
  id: string;
  turn_id: string | null;
  timestamp: string;
  user_id: string;
  agent: string;
  provider: string;
  model: string;
  requested: string;
  fallback_used: number;
  routing_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number;
  outcome: UsageOutcome;
  error: string | null;
}

function toRecord(row: UsageRow): ModelUsageRecord {
  let requested: string[] = [];
  try {
    const parsed = JSON.parse(row.requested) as unknown;
    if (Array.isArray(parsed)) requested = parsed.map(String);
  } catch {
    /* keep empty */
  }
  return {
    id: row.id,
    turnId: row.turn_id,
    timestamp: row.timestamp,
    userId: row.user_id,
    agent: row.agent,
    provider: row.provider,
    model: row.model,
    requested,
    fallbackUsed: row.fallback_used === 1,
    routingReason: row.routing_reason,
    inputTokens: row.input_tokens,
    outputTokens: row.output_tokens,
    totalTokens: row.total_tokens,
    latencyMs: row.latency_ms,
    outcome: row.outcome,
    error: row.error,
  };
}

export class UsageRepo {
  constructor(private db: Db) {}

  record(input: ModelUsageInput): ModelUsageRecord {
    const inputTokens = input.inputTokens ?? null;
    const outputTokens = input.outputTokens ?? null;
    // Derived only when both halves are known; otherwise the total is unknown
    // too, and saying so is better than implying a partial count is complete.
    const totalTokens =
      inputTokens !== null && outputTokens !== null ? inputTokens + outputTokens : null;

    const record: ModelUsageRecord = {
      id: id('use'),
      turnId: input.turnId ?? null,
      timestamp: now(),
      userId: input.userId,
      agent: input.agent,
      provider: input.provider,
      model: input.model,
      requested: input.requested ?? [],
      fallbackUsed: input.fallbackUsed ?? false,
      routingReason: input.routingReason ?? null,
      inputTokens,
      outputTokens,
      totalTokens,
      latencyMs: Math.round(input.latencyMs),
      outcome: input.outcome,
      error: input.error ? truncate(input.error, 500) : null,
    };

    this.db
      .prepare(
        `INSERT INTO model_usage (id, turn_id, timestamp, user_id, agent, provider, model, requested,
           fallback_used, routing_reason, input_tokens, output_tokens, total_tokens, latency_ms,
           outcome, error)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        record.id,
        record.turnId,
        record.timestamp,
        record.userId,
        record.agent,
        record.provider,
        record.model,
        JSON.stringify(record.requested),
        record.fallbackUsed ? 1 : 0,
        record.routingReason,
        record.inputTokens,
        record.outputTokens,
        record.totalTokens,
        record.latencyMs,
        record.outcome,
        record.error,
      );

    return record;
  }

  byTurn(turnId: string): ModelUsageRecord[] {
    const rows = this.db
      .prepare('SELECT * FROM model_usage WHERE turn_id = ? ORDER BY timestamp, rowid')
      .all(turnId) as UsageRow[];
    return rows.map(toRecord);
  }

  list(userId: string, limit = 100): ModelUsageRecord[] {
    const rows = this.db
      .prepare('SELECT * FROM model_usage WHERE user_id = ? ORDER BY timestamp DESC, rowid DESC LIMIT ?')
      .all(userId, limit) as UsageRow[];
    return rows.map(toRecord);
  }

  /**
   * Aggregate totals.
   *
   * `calls` counts every recorded call; the token sums cover only the calls
   * that reported counts, and `callsWithTokens` says how many that was — so a
   * partial total is never mistaken for a complete one.
   */
  summary(userId: string): {
    calls: number;
    callsWithTokens: number;
    inputTokens: number | null;
    outputTokens: number | null;
    totalTokens: number | null;
    averageLatencyMs: number | null;
    errors: number;
    cancelled: number;
  } {
    const row = this.db
      .prepare(
        `SELECT
           COUNT(*)                                            AS calls,
           SUM(CASE WHEN total_tokens IS NOT NULL THEN 1 ELSE 0 END) AS with_tokens,
           SUM(input_tokens)                                   AS input_tokens,
           SUM(output_tokens)                                  AS output_tokens,
           SUM(total_tokens)                                   AS total_tokens,
           AVG(latency_ms)                                     AS avg_latency,
           SUM(CASE WHEN outcome = 'error' THEN 1 ELSE 0 END)  AS errors,
           SUM(CASE WHEN outcome = 'cancelled' THEN 1 ELSE 0 END) AS cancelled
         FROM model_usage WHERE user_id = ?`,
      )
      .get(userId) as Record<string, number | null>;

    return {
      calls: row.calls ?? 0,
      callsWithTokens: row.with_tokens ?? 0,
      inputTokens: row.input_tokens ?? null,
      outputTokens: row.output_tokens ?? null,
      totalTokens: row.total_tokens ?? null,
      averageLatencyMs: row.avg_latency === null ? null : Math.round(row.avg_latency ?? 0),
      errors: row.errors ?? 0,
      cancelled: row.cancelled ?? 0,
    };
  }
}
