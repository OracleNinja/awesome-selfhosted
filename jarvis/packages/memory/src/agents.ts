import type { AgentDefinition, RiskLevel } from '@jarvis/shared';
import { now } from '@jarvis/shared';
import { parseJson, type Db } from './db.ts';

export interface AgentRecord {
  name: string;
  title: string;
  purpose: string;
  maxRisk: RiskLevel;
  readOnly: boolean;
  allowedTools: string[];
  runCount: number;
  lastRunAt: string | null;
}

interface AgentRow {
  name: string;
  title: string;
  purpose: string;
  max_risk: RiskLevel;
  read_only: number;
  allowed_tools: string;
  run_count: number;
  last_run_at: string | null;
}

function toRecord(row: AgentRow): AgentRecord {
  return {
    name: row.name,
    title: row.title,
    purpose: row.purpose,
    maxRisk: row.max_risk,
    readOnly: row.read_only === 1,
    allowedTools: parseJson<string[]>(row.allowed_tools, []),
    runCount: row.run_count,
    lastRunAt: row.last_run_at,
  };
}

/** Registered agents and their run statistics. */
export class AgentRepo {
  constructor(private db: Db) {}

  register(definition: AgentDefinition): void {
    this.db
      .prepare(
        `INSERT INTO agents (name, title, purpose, max_risk, read_only, allowed_tools, run_count, created_at)
         VALUES (?, ?, ?, ?, ?, ?, 0, ?)
         ON CONFLICT(name) DO UPDATE SET
           title = excluded.title,
           purpose = excluded.purpose,
           max_risk = excluded.max_risk,
           read_only = excluded.read_only,
           allowed_tools = excluded.allowed_tools`,
      )
      .run(
        definition.name,
        definition.title,
        definition.purpose,
        definition.maxRisk,
        definition.readOnly ? 1 : 0,
        JSON.stringify(definition.allowedTools),
        now(),
      );
  }

  recordRun(name: string): void {
    this.db
      .prepare('UPDATE agents SET run_count = run_count + 1, last_run_at = ? WHERE name = ?')
      .run(now(), name);
  }

  list(): AgentRecord[] {
    const rows = this.db.prepare('SELECT * FROM agents ORDER BY name').all() as AgentRow[];
    return rows.map(toRecord);
  }

  get(name: string): AgentRecord | null {
    const row = this.db.prepare('SELECT * FROM agents WHERE name = ?').get(name) as
      | AgentRow
      | undefined;
    return row ? toRecord(row) : null;
  }
}
