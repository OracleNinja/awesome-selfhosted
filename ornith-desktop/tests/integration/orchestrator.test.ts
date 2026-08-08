import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { WebContents } from 'electron';
import type { DatabaseSync } from 'node:sqlite';
import { openDatabase } from '../../electron/store/db';
import { createConversationStore, type ConversationStore } from '../../electron/store/conversations';
import { createOrchestrator, type Orchestrator } from '../../electron/chat/orchestrator';
import { DEFAULT_SETTINGS } from '../../shared/defaults';
import type { Settings, ThinkingMode } from '../../shared/types';
import { startStubOllama, type StubHandle } from './stubOllama';

interface Sent {
  channel: string;
  payload: Record<string, unknown>;
}

/** Minimal stand-in for WebContents; the orchestrator only sends and checks destruction. */
function fakeSender() {
  const sent: Sent[] = [];
  const contents = {
    isDestroyed: () => false,
    send: (channel: string, payload: Record<string, unknown>) => sent.push({ channel, payload }),
  } as unknown as WebContents;

  return {
    contents,
    sent,
    of: (channel: string) => sent.filter((s) => s.channel === channel),
    last: (channel: string) => sent.filter((s) => s.channel === channel).at(-1)?.payload,
    joined: (key: 'content' | 'thinking') =>
      sent
        .filter((s) => s.channel === 'chat:delta')
        .map((s) => String(s.payload[key] ?? ''))
        .join(''),
  };
}

function waitForEnd(sender: ReturnType<typeof fakeSender>, timeoutMs = 5000): Promise<void> {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (sender.of('chat:end').length > 0) return resolve();
      if (Date.now() - started > timeoutMs) return reject(new Error('timed out waiting for chat:end'));
      setTimeout(tick, 10);
    };
    tick();
  });
}

describe('chat orchestrator', () => {
  let db: DatabaseSync;
  let store: ConversationStore;
  let orchestrator: Orchestrator;
  let stub: StubHandle;
  let settings: Settings;
  let thinkingMode: ThinkingMode;

  beforeEach(async () => {
    db = openDatabase(':memory:');
    store = createConversationStore(db);
    stub = await startStubOllama();
    thinkingMode = 'structured';
    settings = { ...DEFAULT_SETTINGS, ollamaUrl: stub.url, model: 'ornith-en' };

    orchestrator = createOrchestrator({
      store,
      getSettings: () => settings,
      getThinkingMode: () => thinkingMode,
    });
  });

  afterEach(async () => {
    orchestrator.abortAll();
    await stub.close();
    db.close();
  });

  it('runs a full turn and persists it', async () => {
    const conv = store.create('ornith-en');
    const sender = fakeSender();

    orchestrator.start(
      { conversationId: conv.id, requestId: 'req-1', userText: 'Hello Ornith' },
      sender.contents,
    );
    await waitForEnd(sender);

    const outcome = sender.last('chat:end')!.outcome as { kind: string };
    expect(outcome.kind).toBe('complete');

    const saved = store.get(conv.id)!;
    expect(saved.messages).toHaveLength(2);
    expect(saved.messages[0]).toMatchObject({ role: 'user', content: 'Hello Ornith' });
    expect(saved.messages[1]).toMatchObject({ role: 'assistant', content: 'Hello world', status: 'complete' });
  });

  it('emits chat:started with the store-assigned ids before any delta', async () => {
    const conv = store.create('ornith-en');
    const sender = fakeSender();

    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'hi' }, sender.contents);
    await waitForEnd(sender);

    expect(sender.sent[0].channel).toBe('chat:started');
    const started = sender.last('chat:started')!;
    expect(started.assistantMessage).toMatchObject({ role: 'assistant' });
  });

  it('derives a title from the first message only', async () => {
    const conv = store.create('ornith-en');
    const sender = fakeSender();

    orchestrator.start(
      { conversationId: conv.id, requestId: 'r1', userText: 'Write a Python hello world' },
      sender.contents,
    );
    await waitForEnd(sender);

    expect(store.get(conv.id)!.title).toBe('Write a Python hello world');

    const sender2 = fakeSender();
    orchestrator.start(
      { conversationId: conv.id, requestId: 'r2', userText: 'now in Rust' },
      sender2.contents,
    );
    await waitForEnd(sender2);

    expect(store.get(conv.id)!.title).toBe('Write a Python hello world'); // unchanged
  });

  it('batches deltas rather than emitting one per token (SPEC C3)', async () => {
    await stub.close();
    stub = await startStubOllama({
      script: [
        ...Array.from({ length: 60 }, (_, i) => ({ content: `t${i} ` })),
        { done: true, eval_count: 60, eval_duration: 1_000_000_000 },
      ],
      chunkDelayMs: 1,
    });
    settings = { ...settings, ollamaUrl: stub.url };

    const conv = store.create('ornith-en');
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'go' }, sender.contents);
    await waitForEnd(sender);

    const deltaCount = sender.of('chat:delta').length;
    expect(deltaCount).toBeGreaterThan(0);
    expect(deltaCount).toBeLessThan(60); // coalesced
    // No text lost by the batching.
    expect(sender.joined('content')).toBe(store.get(conv.id)!.messages[1].content);
  });

  it('reports the loading-model state before the first token', async () => {
    await stub.close();
    // Response opens immediately but the first token only arrives after the
    // 2s threshold — what a cold model load looks like.
    stub = await startStubOllama({
      script: [{ content: 'late' }, { done: true, eval_count: 1, eval_duration: 1_000_000_000 }],
      initialDelayMs: 2400,
    });
    settings = { ...settings, ollamaUrl: stub.url };

    const conv = store.create('ornith-en');
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'go' }, sender.contents);
    await waitForEnd(sender, 10_000);

    const states = sender.of('chat:state').map((s) => s.payload.state);
    expect(states).toContain('loading-model');
    expect(states).toContain('streaming');
  }, 15_000);

  it('routes structured thinking into a separate field', async () => {
    await stub.close();
    stub = await startStubOllama({
      script: [
        { thinking: 'reasoning...' },
        { content: 'answer' },
        { done: true, eval_count: 2, eval_duration: 1_000_000_000 },
      ],
    });
    settings = { ...settings, ollamaUrl: stub.url };

    const conv = store.create('ornith-en');
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'go' }, sender.contents);
    await waitForEnd(sender);

    const saved = store.get(conv.id)!.messages[1];
    expect(saved.thinking).toBe('reasoning...');
    expect(saved.content).toBe('answer');
  });

  it('parses inline <think> tags when the model has no structured field', async () => {
    thinkingMode = 'inline';
    await stub.close();
    // Deliberately split the opening tag across chunks.
    stub = await startStubOllama({
      script: [
        { content: '<thi' },
        { content: 'nk>hidden' },
        { content: '</think>visible' },
        { done: true, eval_count: 3, eval_duration: 1_000_000_000 },
      ],
    });
    settings = { ...settings, ollamaUrl: stub.url };

    const conv = store.create('ornith-en');
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'go' }, sender.contents);
    await waitForEnd(sender);

    const saved = store.get(conv.id)!.messages[1];
    expect(saved.thinking).toBe('hidden');
    expect(saved.content).toBe('visible');
    expect(saved.content).not.toContain('think');
  });

  it('keeps partial text and marks the message cancelled on abort', async () => {
    await stub.close();
    stub = await startStubOllama({
      script: Array.from({ length: 40 }, (_, i) => ({ content: `${i} ` })),
      chunkDelayMs: 10,
    });
    settings = { ...settings, ollamaUrl: stub.url };

    const conv = store.create('ornith-en');
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'go' }, sender.contents);

    await new Promise((r) => setTimeout(r, 80));
    orchestrator.abort('r');
    await waitForEnd(sender);

    const outcome = sender.last('chat:end')!.outcome as { kind: string };
    expect(outcome.kind).toBe('cancelled');

    const saved = store.get(conv.id)!.messages[1];
    expect(saved.status).toBe('cancelled');
    expect(saved.content.length).toBeGreaterThan(0); // partial kept
  });

  it('records an error outcome and persists it', async () => {
    await stub.close();
    stub = await startStubOllama({ chatStatus: 404 });
    settings = { ...settings, ollamaUrl: stub.url };

    const conv = store.create('ornith-en');
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'go' }, sender.contents);
    await waitForEnd(sender);

    const outcome = sender.last('chat:end')!.outcome as { kind: string; error: { code: string } };
    expect(outcome.kind).toBe('error');
    expect(outcome.error.code).toBe('MODEL_NOT_FOUND');
    expect(store.get(conv.id)!.messages[1].status).toBe('error');
  });

  it('continues a conversation with prior turns in context', async () => {
    const conv = store.create('ornith-en');

    const first = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'a', userText: 'first' }, first.contents);
    await waitForEnd(first);

    const second = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'b', userText: 'second' }, second.contents);
    await waitForEnd(second);

    const sentMessages = stub.chatRequests.at(-1)!.messages as Array<{ role: string; content: string }>;
    expect(sentMessages.map((m) => m.content)).toEqual([
      'first',
      'Hello world',
      'second',
    ]);
    expect(store.get(conv.id)!.messages).toHaveLength(4);
  });

  it('trims old turns and reports it when the context window is small', async () => {
    const conv = store.create('ornith-en');

    // Seed enough history to exceed the budget: at numCtx 2048 the budget is
    // 1536 tokens, and each 800-char message costs ~229.
    for (let i = 0; i < 10; i += 1) {
      const turn = store.beginTurn(conv.id, 'x'.repeat(800), 'ornith-en');
      store.finalise(turn.assistantMessage.id, {
        content: 'y'.repeat(800),
        thinking: '',
        status: 'complete',
      });
    }

    settings = { ...settings, numCtx: 2048 };
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'final' }, sender.contents);
    await waitForEnd(sender);

    const trimmed = sender.last('chat:trimmed');
    expect(trimmed).toBeDefined();
    expect(Number(trimmed!.droppedMessages)).toBeGreaterThan(0);
  });

  it('rejects a request for a conversation that does not exist', async () => {
    const sender = fakeSender();
    orchestrator.start({ conversationId: 'nope', requestId: 'r', userText: 'hi' }, sender.contents);
    await waitForEnd(sender);

    const outcome = sender.last('chat:end')!.outcome as { kind: string };
    expect(outcome.kind).toBe('error');
  });

  it('abortAll stops everything in flight', async () => {
    await stub.close();
    stub = await startStubOllama({
      script: Array.from({ length: 40 }, () => ({ content: 'x ' })),
      chunkDelayMs: 10,
    });
    settings = { ...settings, ollamaUrl: stub.url };

    const conv = store.create('ornith-en');
    const sender = fakeSender();
    orchestrator.start({ conversationId: conv.id, requestId: 'r', userText: 'go' }, sender.contents);

    await new Promise((r) => setTimeout(r, 30));
    expect(orchestrator.activeCount).toBe(1);
    orchestrator.abortAll();
    expect(orchestrator.activeCount).toBe(0);
  });
});
