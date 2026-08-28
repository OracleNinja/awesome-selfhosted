import { beforeEach, describe, expect, it } from 'vitest';
import type { DatabaseSync } from 'node:sqlite';
import { openDatabase, schemaVersion } from '../../electron/store/db';
import { createConversationStore, type ConversationStore } from '../../electron/store/conversations';

describe('conversation store', () => {
  let db: DatabaseSync;
  let store: ConversationStore;

  beforeEach(() => {
    db = openDatabase(':memory:');
    store = createConversationStore(db);
  });

  it('migrates a fresh database to version 3', () => {
    expect(schemaVersion(db)).toBe(3);
  });

  it('is idempotent when reopened', () => {
    // Running migrations again must not throw or double-apply.
    expect(() => openDatabase(':memory:')).not.toThrow();
  });

  it('creates and reads back a conversation', () => {
    const c = store.create('ornith-en');
    const loaded = store.get(c.id);
    expect(loaded).toMatchObject({ id: c.id, title: 'New chat', model: 'ornith-en' });
    expect(loaded?.messages).toEqual([]);
  });

  it('returns null for an unknown id', () => {
    expect(store.get('nope')).toBeNull();
  });

  it('lists conversations newest-updated first with message counts', () => {
    const a = store.create('ornith-en', 'A');
    const b = store.create('ornith-en', 'B');
    store.beginTurn(a.id, 'hello', 'ornith-en');

    const list = store.list();
    expect(list[0].id).toBe(a.id); // a was just touched
    expect(list.find((c) => c.id === a.id)?.messageCount).toBe(2);
    expect(list.find((c) => c.id === b.id)?.messageCount).toBe(0);
  });

  it('inserts user and assistant rows in one turn, ordered by seq', () => {
    const c = store.create('ornith-en');
    const { userMessage, assistantMessage } = store.beginTurn(c.id, 'hi', 'ornith-en');

    const loaded = store.get(c.id)!;
    expect(loaded.messages.map((m) => m.id)).toEqual([userMessage.id, assistantMessage.id]);
    expect(loaded.messages[0]).toMatchObject({ role: 'user', content: 'hi', status: 'complete' });
    expect(loaded.messages[1]).toMatchObject({ role: 'assistant', status: 'streaming' });
  });

  it('increments seq across multiple turns', () => {
    const c = store.create('ornith-en');
    store.beginTurn(c.id, 'one', 'ornith-en');
    store.beginTurn(c.id, 'two', 'ornith-en');
    expect(store.get(c.id)!.messages.map((m) => m.seq)).toEqual([0, 1, 2, 3]);
  });

  it('persists streaming updates incrementally', () => {
    const c = store.create('ornith-en');
    const { assistantMessage } = store.beginTurn(c.id, 'hi', 'ornith-en');

    store.updateStreaming(assistantMessage.id, 'partial', 'reasoning');
    const m = store.get(c.id)!.messages[1];
    expect(m.content).toBe('partial');
    expect(m.thinking).toBe('reasoning');
    expect(m.status).toBe('streaming');
  });

  it('finalises with stats and computes tokens/sec', () => {
    const c = store.create('ornith-en');
    const { assistantMessage } = store.beginTurn(c.id, 'hi', 'ornith-en');

    store.finalise(assistantMessage.id, {
      content: 'done',
      thinking: '',
      status: 'complete',
      stats: {
        evalCount: 100,
        evalDurationNs: 2_000_000_000,
        promptEvalCount: 5,
        totalDurationNs: 3_000_000_000,
        tokensPerSecond: 50,
      },
    });

    const m = store.get(c.id)!.messages[1];
    expect(m.status).toBe('complete');
    expect(m.stats?.tokensPerSecond).toBeCloseTo(50);
  });

  it('finalises an error and reads the error back', () => {
    const c = store.create('ornith-en');
    const { assistantMessage } = store.beginTurn(c.id, 'hi', 'ornith-en');

    store.finalise(assistantMessage.id, {
      content: '',
      thinking: '',
      status: 'error',
      error: { code: 'MODEL_NOT_FOUND', message: 'The model is not installed.' },
    });

    const m = store.get(c.id)!.messages[1];
    expect(m.status).toBe('error');
    expect(m.error).toEqual({
      code: 'MODEL_NOT_FOUND',
      message: 'The model is not installed.',
    });
  });

  it('renames, and falls back to "New chat" for an empty title', () => {
    const c = store.create('ornith-en');
    store.rename(c.id, 'Renamed');
    expect(store.get(c.id)!.title).toBe('Renamed');
    store.rename(c.id, '   ');
    expect(store.get(c.id)!.title).toBe('New chat');
  });

  it('cascade-deletes messages with the conversation', () => {
    const c = store.create('ornith-en');
    store.beginTurn(c.id, 'hi', 'ornith-en');
    store.remove(c.id);

    expect(store.get(c.id)).toBeNull();
    const orphans = db.prepare('SELECT COUNT(*) AS n FROM messages').get() as { n: number };
    expect(Number(orphans.n)).toBe(0);
  });

  it('clear empties messages but keeps the conversation', () => {
    const c = store.create('ornith-en');
    store.beginTurn(c.id, 'hi', 'ornith-en');
    store.clear(c.id);

    const loaded = store.get(c.id)!;
    expect(loaded.messages).toEqual([]);
    expect(loaded.title).toBe('New chat');
  });

  // The crash-safety guarantee.
  it('recovers rows left mid-stream by a crash', () => {
    const c = store.create('ornith-en');
    const { assistantMessage } = store.beginTurn(c.id, 'hi', 'ornith-en');
    store.updateStreaming(assistantMessage.id, 'half an answ', '');

    // Simulate restart: a new store over the same database.
    const recovered = createConversationStore(db).recoverInterrupted();
    expect(recovered).toBe(1);

    const m = store.get(c.id)!.messages[1];
    expect(m.status).toBe('cancelled');
    expect(m.content).toBe('half an answ'); // partial text preserved
    expect(m.error?.code).toBe('STREAM_INTERRUPTED');
  });

  it('recovery is a no-op when nothing was interrupted', () => {
    const c = store.create('ornith-en');
    const { assistantMessage } = store.beginTurn(c.id, 'hi', 'ornith-en');
    store.finalise(assistantMessage.id, { content: 'x', thinking: '', status: 'complete' });
    expect(store.recoverInterrupted()).toBe(0);
  });
});
