import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { Store, migrate, openDatabase } from '@jarvis/memory';

describe('database', () => {
  it('creates every table JARVIS depends on', () => {
    const store = new Store(':memory:');
    const health = store.health();
    expect(health.ok).toBe(true);
    expect(health.schemaVersion).toBeGreaterThan(0);
    for (const table of [
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
    ]) {
      expect(health.tables).toContain(table);
    }
    store.close();
  });

  it('migrations are idempotent', () => {
    const db = openDatabase(':memory:');
    expect(migrate(db)).toBe(0); // openDatabase already migrated
    db.close();
  });
});

describe('conversations and messages', () => {
  let store: Store;
  const userId = 'user_test';

  beforeEach(() => {
    store = new Store(':memory:');
    store.users.ensure(userId);
  });
  afterEach(() => store.close());

  it('creates conversations and lists them newest first', () => {
    const first = store.conversations.create(userId, 'First');
    const second = store.conversations.create(userId, 'Second');
    store.conversations.touch(second.id);

    const list = store.conversations.list(userId);
    expect(list).toHaveLength(2);
    expect(list[0]!.id).toBe(second.id);
    expect(store.conversations.get(first.id)?.title).toBe('First');
  });

  it('persists messages in order, including tool calls', () => {
    const conversation = store.conversations.create(userId);
    store.messages.append(conversation.id, { role: 'user', content: 'what time is it' });
    store.messages.append(conversation.id, {
      role: 'assistant',
      content: '',
      toolCalls: [{ id: 'c1', name: 'current_time', arguments: { timezone: 'UTC' } }],
    });
    store.messages.append(conversation.id, {
      role: 'tool',
      content: '{"ok":true}',
      toolCallId: 'c1',
      name: 'current_time',
    });

    const messages = store.messages.list(conversation.id);
    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant', 'tool']);
    expect(messages[1]!.toolCalls?.[0]).toEqual({
      id: 'c1',
      name: 'current_time',
      arguments: { timezone: 'UTC' },
    });
    expect(messages[2]!.toolCallId).toBe('c1');
    expect(store.messages.count(conversation.id)).toBe(3);
  });

  it('survives a reopen of the same database file', async () => {
    const { mkdtempSync, rmSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');

    const dir = mkdtempSync(join(tmpdir(), 'jarvis-persist-'));
    const path = join(dir, 'jarvis.db');

    const first = new Store(path);
    first.users.ensure(userId);
    const conversation = first.conversations.create(userId, 'Persisted');
    first.messages.append(conversation.id, { role: 'user', content: 'remember this' });
    first.memories.write({ userId, type: 'fact', content: 'The sky is blue.', source: 'user' });
    first.close();

    const second = new Store(path);
    expect(second.conversations.get(conversation.id)?.title).toBe('Persisted');
    expect(second.messages.list(conversation.id)).toHaveLength(1);
    expect(second.memories.count(userId)).toBe(1);
    second.close();

    rmSync(dir, { recursive: true, force: true });
  });

  it('auto-titles a new conversation from the first message', () => {
    const conversation = store.conversations.create(userId);
    store.conversations.autoTitle(conversation.id, 'Plan the Q3 restaurant staffing rota');
    expect(store.conversations.get(conversation.id)?.title).toBe('Plan the Q3 restaurant staffing rota');

    // A second message must not rename it.
    store.conversations.autoTitle(conversation.id, 'something else entirely');
    expect(store.conversations.get(conversation.id)?.title).toBe('Plan the Q3 restaurant staffing rota');
  });

  it('deleting a conversation removes its messages', () => {
    const conversation = store.conversations.create(userId);
    store.messages.append(conversation.id, { role: 'user', content: 'hello' });
    store.conversations.delete(conversation.id);
    expect(store.conversations.get(conversation.id)).toBeNull();
    expect(store.messages.list(conversation.id)).toHaveLength(0);
  });
});

describe('memory', () => {
  let store: Store;
  const userId = 'user_test';

  beforeEach(() => {
    store = new Store(':memory:');
    store.users.ensure(userId);
  });
  afterEach(() => store.close());

  it('stores every declared field', () => {
    const memory = store.memories.write({
      userId,
      type: 'preference',
      content: 'Prefers espresso over filter coffee.',
      source: 'agent:jarvis',
      confidence: 0.9,
      importance: 0.7,
      tags: ['coffee'],
    });

    expect(memory.id).toMatch(/^mem_/);
    expect(memory.type).toBe('preference');
    expect(memory.source).toBe('agent:jarvis');
    expect(memory.confidence).toBe(0.9);
    expect(memory.importance).toBe(0.7);
    expect(memory.createdAt).toBeTruthy();
    expect(memory.updatedAt).toBeTruthy();

    const reloaded = store.memories.get(memory.id);
    expect(reloaded?.tags).toEqual(['coffee']);
  });

  it('retrieves by relevance, best match first', () => {
    store.memories.write({ userId, type: 'project', content: 'Building JARVIS, a personal AI operating system.', source: 'user' });
    store.memories.write({ userId, type: 'person', content: 'Marco is the head chef at the restaurant.', source: 'user' });
    store.memories.write({ userId, type: 'fact', content: 'The restaurant opens at 11am on weekdays.', source: 'user' });

    const results = store.memories.search(userId, 'who is the head chef at the restaurant');
    expect(results.length).toBeGreaterThan(0);
    expect(results[0]!.content).toContain('Marco');
    expect(results[0]!.score).toBeGreaterThan(0);
  });

  it('returns nothing for an unrelated query rather than a bad guess', () => {
    store.memories.write({ userId, type: 'fact', content: 'The restaurant opens at 11am.', source: 'user' });
    expect(store.memories.search(userId, 'quantum chromodynamics lattice')).toHaveLength(0);
  });

  it('deduplicates identical content and keeps the stronger signal', () => {
    const first = store.memories.write({
      userId,
      type: 'fact',
      content: 'Deadline is 30 June.',
      source: 'user',
      importance: 0.4,
      confidence: 0.5,
    });
    const second = store.memories.write({
      userId,
      type: 'fact',
      content: 'Deadline is 30 June.',
      source: 'agent:scout',
      importance: 0.9,
      confidence: 0.7,
    });

    expect(second.id).toBe(first.id);
    expect(store.memories.count(userId)).toBe(1);
    expect(second.importance).toBe(0.9);
    expect(second.confidence).toBe(0.7);
  });

  it('expires temporary memories and excludes them from reads', () => {
    store.memories.write({
      userId,
      type: 'temporary',
      content: 'User is on a call until 3pm.',
      source: 'user',
      expiresAt: new Date(Date.now() - 1000).toISOString(),
    });
    store.memories.write({ userId, type: 'fact', content: 'Permanent fact.', source: 'user' });

    expect(store.memories.list(userId)).toHaveLength(1);
    expect(store.memories.list(userId)[0]!.content).toBe('Permanent fact.');
  });

  it('filters by type', () => {
    store.memories.write({ userId, type: 'goal', content: 'Ship JARVIS 0.1.', source: 'user' });
    store.memories.write({ userId, type: 'fact', content: 'Node 22 is installed.', source: 'user' });

    const goals = store.memories.list(userId, { type: 'goal' });
    expect(goals).toHaveLength(1);
    expect(goals[0]!.type).toBe('goal');
  });

  it('deletes a memory', () => {
    const memory = store.memories.write({ userId, type: 'fact', content: 'Delete me.', source: 'user' });
    expect(store.memories.delete(memory.id)).toBe(true);
    expect(store.memories.get(memory.id)).toBeNull();
  });

  it('rejects empty content', () => {
    expect(() =>
      store.memories.write({ userId, type: 'fact', content: '   ', source: 'user' }),
    ).toThrow(/must not be empty/);
  });
});

describe('tasks', () => {
  it('creates, lists, filters and updates', () => {
    const store = new Store(':memory:');
    const userId = 'user_test';
    store.users.ensure(userId);

    const task = store.tasks.create({ userId, title: 'Order stock', priority: 'high' });
    expect(task.status).toBe('open');

    store.tasks.create({ userId, title: 'Second task' });
    expect(store.tasks.list(userId)).toHaveLength(2);

    store.tasks.update(task.id, { status: 'done' });
    expect(store.tasks.list(userId, { status: 'done' })).toHaveLength(1);
    expect(store.tasks.get(task.id)?.status).toBe('done');
    store.close();
  });
});
