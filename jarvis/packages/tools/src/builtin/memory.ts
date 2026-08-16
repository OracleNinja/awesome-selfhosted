import type { MemoryType, ToolDefinition } from '@jarvis/shared';
import { MEMORY_TYPES, secondsFromNow, truncate } from '@jarvis/shared';
import type { Store } from '@jarvis/memory';

/**
 * Memory tools.
 *
 * Writes are explicit and model-initiated: JARVIS does not persist every
 * message. That keeps the memory store meaningful rather than a transcript
 * copy, and it makes "what does JARVIS know about me" answerable.
 */

export function memorySearchTool(store: Store): ToolDefinition {
  return {
    name: 'memory_search',
    description:
      'Search long-term memory for facts, preferences, goals, projects, people and standing instructions previously saved about the user.',
    risk: 'READ',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'What to look for.', minLength: 1 },
        type: {
          type: 'string',
          description: 'Restrict the search to one memory type.',
          enum: [...MEMORY_TYPES],
        },
        limit: { type: 'integer', description: 'Maximum results (1-25).', minimum: 1, maximum: 25, default: 8 },
      },
      required: ['query'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const query = String(args.query ?? '');
      const options: { limit: number; type?: MemoryType } = {
        limit: typeof args.limit === 'number' ? args.limit : 8,
      };
      if (typeof args.type === 'string') options.type = args.type as MemoryType;

      const results = store.memories.search(ctx.userId, query, options);
      if (results.length === 0) {
        return {
          ok: true,
          summary: `No memories matched "${truncate(query, 60)}".`,
          data: { query, results: [] },
        };
      }
      return {
        ok: true,
        summary: `Found ${results.length} memor${results.length === 1 ? 'y' : 'ies'} matching "${truncate(query, 60)}".`,
        data: {
          query,
          results: results.map((memory) => ({
            id: memory.id,
            type: memory.type,
            content: memory.content,
            importance: memory.importance,
            confidence: memory.confidence,
            score: memory.score,
            updatedAt: memory.updatedAt,
          })),
        },
      };
    },
  };
}

export function memoryWriteTool(store: Store): ToolDefinition {
  return {
    name: 'memory_write',
    description:
      'Save a durable fact about the user to long-term memory. Use it for things that will still matter next week: preferences, goals, projects, people, standing instructions. Do not save small talk or things the user can see in the conversation.',
    risk: 'WRITE',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        type: {
          type: 'string',
          description: 'Kind of memory this is.',
          enum: [...MEMORY_TYPES],
        },
        content: {
          type: 'string',
          description: 'The memory, written as a self-contained statement.',
          minLength: 3,
          maxLength: 2000,
        },
        importance: {
          type: 'number',
          description: '0-1. How much this should shape future answers.',
          minimum: 0,
          maximum: 1,
          default: 0.5,
        },
        confidence: {
          type: 'number',
          description: '0-1. How certain you are this is true.',
          minimum: 0,
          maximum: 1,
          default: 0.8,
        },
        tags: { type: 'array', description: 'Optional labels.', items: { type: 'string' } },
        expires_in_hours: {
          type: 'number',
          description: 'For type "temporary": hours until it is forgotten.',
          minimum: 0.1,
          maximum: 8760,
        },
      },
      required: ['type', 'content'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const type = args.type as MemoryType;
      const content = String(args.content ?? '').trim();
      if (!content) {
        return { ok: false, summary: 'Memory not saved.', error: 'content must not be empty' };
      }

      const expiresAt =
        type === 'temporary' && typeof args.expires_in_hours === 'number'
          ? secondsFromNow(args.expires_in_hours * 3600)
          : type === 'temporary'
            ? secondsFromNow(24 * 3600)
            : null;

      const memory = store.memories.write({
        userId: ctx.userId,
        type,
        content,
        source: `agent:${ctx.agent}`,
        importance: typeof args.importance === 'number' ? args.importance : 0.5,
        confidence: typeof args.confidence === 'number' ? args.confidence : 0.8,
        tags: Array.isArray(args.tags) ? args.tags.map(String) : [],
        expiresAt,
      });

      return {
        ok: true,
        summary: `Saved ${type} memory: ${truncate(content, 80)}`,
        data: { id: memory.id, type: memory.type, expiresAt: memory.expiresAt },
      };
    },
  };
}

export function memoryForgetTool(store: Store): ToolDefinition {
  return {
    name: 'memory_forget',
    description:
      'Permanently delete a memory by id. Irreversible — find the id with memory_search first and confirm it is the right one.',
    risk: 'DESTRUCTIVE',
    requiresApproval: true,
    inputSchema: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Memory id, as returned by memory_search.', minLength: 3 },
      },
      required: ['id'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const memoryId = String(args.id ?? '');
      const memory = store.memories.get(memoryId);
      if (!memory || memory.userId !== ctx.userId) {
        return { ok: false, summary: 'No such memory.', error: `memory "${memoryId}" was not found` };
      }
      store.memories.delete(memoryId);
      return {
        ok: true,
        summary: `Deleted ${memory.type} memory: ${truncate(memory.content, 80)}`,
        data: { id: memoryId },
      };
    },
  };
}
