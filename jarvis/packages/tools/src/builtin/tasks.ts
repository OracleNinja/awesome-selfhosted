import type { TaskPriority, TaskStatus, ToolDefinition } from '@jarvis/shared';
import { truncate } from '@jarvis/shared';
import type { Store } from '@jarvis/memory';

export function taskCreateTool(store: Store): ToolDefinition {
  return {
    name: 'task_create',
    description: 'Create a task on the user’s task list.',
    risk: 'WRITE',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Short imperative title.', minLength: 2, maxLength: 200 },
        detail: { type: 'string', description: 'Optional detail or context.', maxLength: 4000 },
        priority: {
          type: 'string',
          description: 'Task priority.',
          enum: ['low', 'normal', 'high', 'urgent'],
          default: 'normal',
        },
        assigned_agent: {
          type: 'string',
          description: 'Agent expected to carry the task out.',
          enum: ['scout', 'operator', 'advisor', 'developer'],
        },
        due_at: { type: 'string', description: 'ISO-8601 due date, e.g. 2026-01-31T17:00:00Z.' },
      },
      required: ['title'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const dueAt = typeof args.due_at === 'string' ? args.due_at : null;
      if (dueAt && Number.isNaN(Date.parse(dueAt))) {
        return { ok: false, summary: 'Task not created.', error: `due_at "${dueAt}" is not a valid date` };
      }

      const task = store.tasks.create({
        userId: ctx.userId,
        title: String(args.title ?? ''),
        detail: typeof args.detail === 'string' ? args.detail : '',
        priority: (typeof args.priority === 'string' ? args.priority : 'normal') as TaskPriority,
        assignedAgent: typeof args.assigned_agent === 'string' ? args.assigned_agent : null,
        dueAt,
      });

      return {
        ok: true,
        summary: `Created task: ${truncate(task.title, 80)}`,
        data: { id: task.id, title: task.title, priority: task.priority, status: task.status },
      };
    },
  };
}

export function taskListTool(store: Store): ToolDefinition {
  return {
    name: 'task_list',
    description: 'List the user’s tasks, optionally filtered by status.',
    risk: 'READ',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        status: {
          type: 'string',
          description: 'Only return tasks in this state.',
          enum: ['open', 'in_progress', 'blocked', 'done', 'cancelled'],
        },
        limit: { type: 'integer', minimum: 1, maximum: 100, default: 20 },
      },
      required: [],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const options: { status?: TaskStatus; limit: number } = {
        limit: typeof args.limit === 'number' ? args.limit : 20,
      };
      if (typeof args.status === 'string') options.status = args.status as TaskStatus;

      const tasks = store.tasks.list(ctx.userId, options);
      return {
        ok: true,
        summary:
          tasks.length === 0
            ? 'No tasks found.'
            : `${tasks.length} task${tasks.length === 1 ? '' : 's'}.`,
        data: {
          tasks: tasks.map((task) => ({
            id: task.id,
            title: task.title,
            status: task.status,
            priority: task.priority,
            assignedAgent: task.assignedAgent,
            dueAt: task.dueAt,
          })),
        },
      };
    },
  };
}

export function taskUpdateTool(store: Store): ToolDefinition {
  return {
    name: 'task_update',
    description: 'Update a task’s status, priority, detail or assignee.',
    risk: 'WRITE',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Task id.', minLength: 3 },
        status: {
          type: 'string',
          enum: ['open', 'in_progress', 'blocked', 'done', 'cancelled'],
        },
        priority: { type: 'string', enum: ['low', 'normal', 'high', 'urgent'] },
        detail: { type: 'string', maxLength: 4000 },
        assigned_agent: {
          type: 'string',
          enum: ['scout', 'operator', 'advisor', 'developer'],
        },
      },
      required: ['id'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const taskId = String(args.id ?? '');
      const existing = store.tasks.get(taskId);
      if (!existing || existing.userId !== ctx.userId) {
        return { ok: false, summary: 'No such task.', error: `task "${taskId}" was not found` };
      }

      const patch: Parameters<Store['tasks']['update']>[1] = {};
      if (typeof args.status === 'string') patch.status = args.status as TaskStatus;
      if (typeof args.priority === 'string') patch.priority = args.priority as TaskPriority;
      if (typeof args.detail === 'string') patch.detail = args.detail;
      if (typeof args.assigned_agent === 'string') patch.assignedAgent = args.assigned_agent;

      const updated = store.tasks.update(taskId, patch);
      return {
        ok: true,
        summary: `Updated task "${truncate(existing.title, 60)}" → ${updated?.status ?? existing.status}`,
        data: updated,
      };
    },
  };
}
