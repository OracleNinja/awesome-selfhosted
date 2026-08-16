/**
 * The orchestration loop: routing, memory context, tool execution, honesty.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createTestJarvis, ScriptedProvider, type TestHarness } from './helpers.ts';

describe('orchestrator', () => {
  let harness: TestHarness;

  afterEach(() => harness?.cleanup());

  it('answers directly and persists both sides of the exchange', async () => {
    harness = createTestJarvis({ turns: [{ content: 'Systems nominal.' }] });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'status report',
    });

    expect(turn.reply).toBe('Systems nominal.');
    expect(turn.conversationId).toMatch(/^conv_/);

    const stored = harness.jarvis.store.messages.list(turn.conversationId);
    expect(stored.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(stored[0]!.content).toBe('status report');
    expect(stored[1]!.content).toBe('Systems nominal.');
  });

  it('continues an existing conversation', async () => {
    harness = createTestJarvis({ turns: [{ content: 'One.' }, { content: 'Two.' }] });

    const first = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'first message',
    });
    const second = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      conversationId: first.conversationId,
      text: 'second message',
    });

    expect(second.conversationId).toBe(first.conversationId);
    expect(harness.jarvis.store.messages.list(first.conversationId)).toHaveLength(4);

    // The model saw the earlier turn.
    const history = harness.provider.lastRequest()!.messages.map((m) => m.content);
    expect(history).toContain('first message');
    expect(history).toContain('One.');
  });

  it('runs a tool the model asks for and feeds the result back', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'current_time', arguments: { timezone: 'UTC' } }] },
        { content: 'It is currently midday UTC.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'what time is it?',
    });

    expect(turn.toolCalls).toEqual([{ name: 'current_time', status: 'executed' }]);
    expect(turn.reply).toBe('It is currently midday UTC.');

    const secondRequest = harness.provider.requests[1]!;
    const toolMessage = secondRequest.messages.find((m) => m.role === 'tool');
    expect(toolMessage?.content).toMatch(/"ok":true/);

    const events = turn.events.map((event) => event.type);
    expect(events).toContain('USER_MESSAGE');
    expect(events).toContain('TOOL_REQUEST');
    expect(events).toContain('TOOL_RESULT');
    expect(events).toContain('MODEL_RESPONSE');
  });

  it('injects relevant memories into the system prompt', async () => {
    harness = createTestJarvis({ turns: [{ content: 'Noted.' }] });
    harness.jarvis.store.memories.write({
      userId: harness.userId,
      type: 'preference',
      content: 'Prefers metric units.',
      source: 'user',
      importance: 0.9,
    });

    await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'what units should you use, metric or imperial?',
    });

    const systemPrompt = harness.provider.systemPromptOf(harness.provider.requests[0]);
    expect(systemPrompt).toContain('Prefers metric units.');
    expect(systemPrompt).toContain('You are JARVIS');
  });

  it('says memory is empty rather than implying knowledge it lacks', async () => {
    harness = createTestJarvis({ turns: [{ content: 'I have nothing on file.' }] });
    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'what do you know about me' });
    const systemPrompt = harness.provider.systemPromptOf(harness.provider.requests[0]);
    expect(systemPrompt).toMatch(/holds nothing relevant/);
  });

  it('tells the user plainly when the model provider is not configured', async () => {
    const provider = new ScriptedProvider([]).setAvailable(false);
    harness = createTestJarvis({ provider });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'hello',
    });

    expect(turn.error).toBeTruthy();
    expect(turn.reply).toMatch(/not configured/i);
    // The failure is recorded in the conversation, not swallowed.
    const stored = harness.jarvis.store.messages.list(turn.conversationId);
    expect(stored[1]!.content).toMatch(/not configured/i);
  });

  it('reports a model call failure without claiming anything was done', async () => {
    const provider = new ScriptedProvider([{ content: 'unused' }]);
    harness = createTestJarvis({ provider });
    provider.failNext('connection reset by peer');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'do the thing',
    });

    expect(turn.error).toMatch(/connection reset/);
    expect(turn.reply).toMatch(/Nothing was executed/);
  });

  it('surfaces a tool failure to the model as an explicit failure', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_read', arguments: { path: 'missing.txt' } }] },
        { content: 'That file does not exist.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'read missing.txt',
    });

    expect(turn.toolCalls[0]!.status).toBe('error');
    const toolMessage = harness.provider.requests[1]!.messages.find((m) => m.role === 'tool');
    expect(toolMessage?.content).toMatch(/"ok":false/);
    expect(toolMessage?.content).toMatch(/did NOT succeed/);
  });

  it('rejects invalid tool arguments and tells the model why', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'memory_write', arguments: { type: 'nonsense', content: 'x' } }] },
        { content: 'I could not save that.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'remember something',
    });

    expect(turn.toolCalls[0]!.status).toBe('error');
    const toolMessage = harness.provider.requests[1]!.messages.find((m) => m.role === 'tool');
    expect(toolMessage?.content).toMatch(/Invalid arguments/);
    expect(harness.jarvis.store.memories.count(harness.userId)).toBe(0);
  });

  it('stops at the iteration limit instead of looping forever', async () => {
    const turns = Array.from({ length: 20 }, () => ({
      toolCalls: [{ name: 'current_time', arguments: {} }],
    }));
    harness = createTestJarvis({ turns });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'loop forever',
    });

    expect(turn.iterations).toBe(harness.jarvis.jarvisAgent.maxIterations);
    expect(turn.reply).toMatch(/step limit/i);
  });

  it('drops orphaned tool messages when history is trimmed', async () => {
    harness = createTestJarvis({ turns: [{ content: 'ok' }] });
    const conversation = harness.jarvis.store.conversations.create(harness.userId);
    // A tool message whose assistant call is not in history.
    harness.jarvis.store.messages.append(conversation.id, {
      role: 'tool',
      content: 'orphan',
      toolCallId: 'missing_call',
    });

    await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      conversationId: conversation.id,
      text: 'hello',
    });

    const sent = harness.provider.requests[0]!.messages;
    expect(sent.some((m) => m.content === 'orphan')).toBe(false);
  });
});
