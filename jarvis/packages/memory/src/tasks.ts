import type { Task, TaskPriority, TaskStatus } from '@jarvis/shared';
import { id, now } from '@jarvis/shared';
import type { Db } from './db.ts';

interface TaskRow {
  id: string;
  user_id: string;
  title: string;
  detail: string;
  status: TaskStatus;
  priority: TaskPriority;
  assigned_agent: string | null;
  due_at: string | null;
  created_at: string;
  updated_at: string;
}

function toTask(row: TaskRow): Task {
  return {
    id: row.id,
    userId: row.user_id,
    title: row.title,
    detail: row.detail,
    status: row.status,
    priority: row.priority,
    assignedAgent: row.assigned_agent,
    dueAt: row.due_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export interface TaskCreateInput {
  userId: string;
  title: string;
  detail?: string;
  priority?: TaskPriority;
  assignedAgent?: string | null;
  dueAt?: string | null;
}

export class TaskRepo {
  constructor(private db: Db) {}

  create(input: TaskCreateInput): Task {
    const timestamp = now();
    const task: Task = {
      id: id('task'),
      userId: input.userId,
      title: input.title,
      detail: input.detail ?? '',
      status: 'open',
      priority: input.priority ?? 'normal',
      assignedAgent: input.assignedAgent ?? null,
      dueAt: input.dueAt ?? null,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    this.db
      .prepare(
        `INSERT INTO tasks (id, user_id, title, detail, status, priority, assigned_agent, due_at, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        task.id,
        task.userId,
        task.title,
        task.detail,
        task.status,
        task.priority,
        task.assignedAgent,
        task.dueAt,
        task.createdAt,
        task.updatedAt,
      );
    return task;
  }

  get(taskId: string): Task | null {
    const row = this.db.prepare('SELECT * FROM tasks WHERE id = ?').get(taskId) as
      | TaskRow
      | undefined;
    return row ? toTask(row) : null;
  }

  list(userId: string, options: { status?: TaskStatus; limit?: number } = {}): Task[] {
    const limit = options.limit ?? 50;
    const rows = options.status
      ? (this.db
          .prepare(
            'SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?',
          )
          .all(userId, options.status, limit) as TaskRow[])
      : (this.db
          .prepare('SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?')
          .all(userId, limit) as TaskRow[]);
    return rows.map(toTask);
  }

  update(
    taskId: string,
    patch: Partial<Pick<Task, 'title' | 'detail' | 'status' | 'priority' | 'assignedAgent' | 'dueAt'>>,
  ): Task | null {
    const existing = this.get(taskId);
    if (!existing) return null;
    const merged: Task = { ...existing, ...patch, updatedAt: now() };
    this.db
      .prepare(
        `UPDATE tasks SET title = ?, detail = ?, status = ?, priority = ?, assigned_agent = ?,
           due_at = ?, updated_at = ? WHERE id = ?`,
      )
      .run(
        merged.title,
        merged.detail,
        merged.status,
        merged.priority,
        merged.assignedAgent,
        merged.dueAt,
        merged.updatedAt,
        taskId,
      );
    return merged;
  }
}
