import { openDatabase, listTables, schemaVersion, type Db } from './db.ts';
import { ConversationRepo, MessageRepo, UserRepo } from './conversations.ts';
import { MemoryRepo } from './memories.ts';
import { TaskRepo } from './tasks.ts';
import { ApprovalRepo } from './approvals.ts';
import { AuditRepo, EventRepo } from './audit.ts';
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
