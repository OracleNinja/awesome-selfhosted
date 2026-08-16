import { openDatabase, listTables, schemaVersion, type Db } from './db.ts';
import { ConversationRepo, MessageRepo, UserRepo } from './conversations.ts';
import { MemoryRepo } from './memories.ts';
import { TaskRepo } from './tasks.ts';
import { ApprovalRepo } from './approvals.ts';
import { AuditRepo, EventRepo } from './audit.ts';
import { UsageRepo } from './usage.ts';
import { AgentRepo } from './agents.ts';

/**
 * The single persistence surface handed to the orchestrator, tools and agents.
 * Nothing outside this package touches SQL.
 */
export class Store {
  readonly db: Db;
  readonly users: UserRepo;
  readonly conversations: ConversationRepo;
  readonly messages: MessageRepo;
  readonly memories: MemoryRepo;
  readonly tasks: TaskRepo;
  readonly approvals: ApprovalRepo;
  readonly audit: AuditRepo;
  readonly events: EventRepo;
  readonly agents: AgentRepo;
  readonly usage: UsageRepo;

  constructor(path: string) {
    this.db = openDatabase(path);
    this.users = new UserRepo(this.db);
    this.conversations = new ConversationRepo(this.db);
    this.messages = new MessageRepo(this.db);
    this.memories = new MemoryRepo(this.db);
    this.tasks = new TaskRepo(this.db);
    this.approvals = new ApprovalRepo(this.db);
    this.audit = new AuditRepo(this.db);
    this.events = new EventRepo(this.db);
    this.agents = new AgentRepo(this.db);
    this.usage = new UsageRepo(this.db);
  }

  /**
   * Tool calls belonging to one turn, oldest first.
   *
   * Lives here rather than in a repo because `tool_calls` is a mirror table
   * written by AuditRepo; this is the only read of it by turn.
   */
  toolCallsByTurn(turnId: string): {
    id: string;
    turnId: string | null;
    conversationId: string | null;
    agent: string;
    tool: string;
    risk: string;
    state: string;
    error: string | null;
    durationMs: number;
    createdAt: string;
  }[] {
    const rows = this.db
      .prepare('SELECT * FROM tool_calls WHERE turn_id = ? ORDER BY created_at, rowid')
      .all(turnId) as Record<string, unknown>[];
    return rows.map((row) => ({
      id: String(row.id),
      turnId: (row.turn_id as string | null) ?? null,
      conversationId: (row.conversation_id as string | null) ?? null,
      agent: String(row.agent),
      tool: String(row.tool),
      risk: String(row.risk),
      state: String(row.state),
      error: (row.error as string | null) ?? null,
      durationMs: Number(row.duration_ms ?? 0),
      createdAt: String(row.created_at),
    }));
  }

  health(): { ok: boolean; schemaVersion: number; tables: string[] } {
    const tables = listTables(this.db);
    const required = [
      'users',
      'conversations',
      'messages',
      'memories',
      'tasks',
      'agents',
      'tool_calls',
      'approvals',
      'audit_events',
      'events',
      'model_usage',
    ];
    return {
      ok: required.every((table) => tables.includes(table)),
      schemaVersion: schemaVersion(this.db),
      tables,
    };
  }

  close(): void {
    this.db.close();
  }
}

export function createStore(path: string): Store {
  return new Store(path);
}
