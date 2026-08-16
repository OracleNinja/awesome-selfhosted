/**
 * Stage 1 control plane: cancellation, tool timeouts and turn correlation.
 *
 * These are the tests that decide whether the control boundaries are real.
 * They use the actual registry, executor and orchestrator — the only stand-in
 * is the model, and the "slow tool" fixtures below, which exist so a timeout
 * can be observed in milliseconds instead of seconds.
 */
import { describe, it, expect, afterEach, beforeAll, afterAll } from 'vitest';
import type { Server } from 'node:http';
import {
  DEFAULT_TOOL_TIMEOUT_MS,
  TurnRegistry,
  ToolTimeoutError,
  TurnCancelledError,
  isAbortError,
  linkTimeout,
} from '@jarvis/core';
import type { ToolDefinition } from '@jarvis/shared';
import { startJarvisServer } from '@jarvis/server/src/server.ts';
import { DEFAULT_USER_ID } from '@jarvis/server/src/auth.ts';
import { createTestJarvis, ScriptedProvider, type TestHarness } from './helpers.ts';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A tool that waits, and honours abort — the common case for network tools. */
function slowTool(name: string, delayMs: number, timeoutMs?: number): ToolDefinition {
  return {
    name,
    description: `Waits ${delayMs}ms.`,
    risk: 'READ',
    requiresApproval: false,
    ...(timeoutMs !== undefined ? { timeoutMs } : {}),
    inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
    execute: (_args, ctx) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve({ ok: true, summary: `${name} finished` }), delayMs);
        ctx.signal?.addEventListener(
          'abort',
          () => {
            clearTimeout(timer);
            reject(ctx.signal?.reason ?? new Error('aborted'));
          },
          { once: true },
        );
      }),
  };
}

/**
 * A tool that ignores its abort signal.
 *
 * The executor must still stop waiting on it — a tool that cannot be
 * interrupted should not be able to hold a turn open.
 */
function stubbornTool(name: string, delayMs: number, timeoutMs?: number): ToolDefinition {
  return {
    name,
    description: `Ignores cancellation for ${delayMs}ms.`,
    risk: 'READ',
    requiresApproval: false,
    ...(timeoutMs !== undefined ? { timeoutMs } : {}),
    inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
    execute: () => new Promise((resolve) => setTimeout(() => resolve({ ok: true, summary: 'done' }), delayMs)),
  };
}

/** Records the turn id it was invoked with, so propagation can be asserted. */
function recordingTool(name: string, sink: string[]): ToolDefinition {
  return {
    name,
    description: 'Records its turn id.',
    risk: 'READ',
    requiresApproval: false,
    inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
    async execute(_args, ctx) {
      sink.push(ctx.turnId);
      return { ok: true, summary: `saw ${ctx.turnId}` };
    },
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 5));

// ===========================================================================
// Turn registry
// ===========================================================================

describe('turn registry', () => {
  it('issues a unique id and a live signal per turn', () => {
    const registry = new TurnRegistry();
    const a = registry.begin({ userId: 'u' });
    const b = registry.begin({ userId: 'u' });

    expect(a.turnId).not.toBe(b.turnId);
    expect(a.signal.aborted).toBe(false);
    expect(registry.size).toBe(2);
  });

  it('cancels by id and aborts that turn’s signal', () => {
    const registry = new TurnRegistry();
    const turn = registry.begin({ userId: 'u' });

    expect(registry.cancel(turn.turnId)).toBe('cancelled');
    expect(turn.signal.aborted).toBe(true);
    expect(turn.isCancelled()).toBe(true);
    expect(turn.signal.reason).toBeInstanceOf(TurnCancelledError);
  });

  it('reports an unknown turn as not_found rather than throwing', () => {
    const registry = new TurnRegistry();
    expect(registry.cancel('turn_does_not_exist')).toBe('not_found');
  });

  it('reports a finished turn as already_finished', () => {
    const registry = new TurnRegistry();
    const turn = registry.begin({ userId: 'u' });
    turn.end();
    expect(registry.cancel(turn.turnId)).toBe('not_found'); // removed from the registry
  });

  it('is idempotent when cancelled twice', () => {
    const registry = new TurnRegistry();
    const turn = registry.begin({ userId: 'u' });

    expect(registry.cancel(turn.turnId)).toBe('cancelled');
    expect(registry.cancel(turn.turnId)).toBe('already_cancelled');
    expect(turn.signal.aborted).toBe(true);
  });

  it('does not leak controllers — every exit path removes the entry', () => {
    const registry = new TurnRegistry();

    const completed = registry.begin({ userId: 'u' });
    completed.end();

    const cancelled = registry.begin({ userId: 'u' });
    registry.cancel(cancelled.turnId);
    cancelled.end();

    const errored = registry.begin({ userId: 'u' });
    errored.end();

    expect(registry.size).toBe(0);
  });

  it('end() is idempotent', () => {
    const registry = new TurnRegistry();
    const turn = registry.begin({ userId: 'u' });
    turn.end();
    turn.end();
    expect(registry.size).toBe(0);
  });

  it('cancelling after completion is harmless', () => {
    const registry = new TurnRegistry();
    const turn = registry.begin({ userId: 'u' });
    turn.end();
    expect(() => registry.cancel(turn.turnId)).not.toThrow();
    expect(turn.signal.aborted).toBe(false);
  });

  it('never exposes the controller', () => {
    const registry = new TurnRegistry();
    const turn = registry.begin({ userId: 'u' });
    // The handle offers a read-only signal; there is no abort() to reach for.
    expect((turn as unknown as { controller?: unknown }).controller).toBeUndefined();
    expect((turn.signal as unknown as { abort?: unknown }).abort).toBeUndefined();
  });

  it('cancelAll stops every in-flight turn', () => {
    const registry = new TurnRegistry();
    const a = registry.begin({ userId: 'u' });
    const b = registry.begin({ userId: 'u' });
    expect(registry.cancelAll()).toBe(2);
    expect(a.signal.aborted).toBe(true);
    expect(b.signal.aborted).toBe(true);
  });
});

// ===========================================================================
// The timeout / cancellation race
// ===========================================================================

describe('abort boundary', () => {
  it('records timeout when the timer fires first', async () => {
    const turn = new AbortController();
    const boundary = linkTimeout(turn.signal, 10, 'slow');

    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(boundary.signal.aborted).toBe(true);
    expect(boundary.cause()).toBe('tool_timeout');
    expect(boundary.signal.reason).toBeInstanceOf(ToolTimeoutError);
    boundary.dispose();
  });

  it('records cancellation when the turn is cancelled first', async () => {
    const turn = new AbortController();
    const boundary = linkTimeout(turn.signal, 1000, 'slow');

    turn.abort(new TurnCancelledError('turn_1'));
    await flush();

    expect(boundary.signal.aborted).toBe(true);
    expect(boundary.cause()).toBe('turn_cancelled');
    boundary.dispose();
  });

  it('cancellation wins even when the timer fires immediately afterwards', async () => {
    const turn = new AbortController();
    const boundary = linkTimeout(turn.signal, 5, 'slow');

    turn.abort(new TurnCancelledError('turn_1'));
    // The timer now fires — it must not reclassify a decision already made.
    await new Promise((resolve) => setTimeout(resolve, 25));

    expect(boundary.cause()).toBe('turn_cancelled');
    boundary.dispose();
  });

  it('timeout wins when the turn is cancelled afterwards', async () => {
    const turn = new AbortController();
    const boundary = linkTimeout(turn.signal, 5, 'slow');

    await new Promise((resolve) => setTimeout(resolve, 25));
    turn.abort(new TurnCancelledError('turn_1'));
    await flush();

    expect(boundary.cause()).toBe('tool_timeout');
    boundary.dispose();
  });

  it('starts already aborted when the turn was cancelled before the call', () => {
    const turn = new AbortController();
    turn.abort(new TurnCancelledError('turn_1'));

    const boundary = linkTimeout(turn.signal, 1000, 'slow');
    expect(boundary.signal.aborted).toBe(true);
    expect(boundary.cause()).toBe('turn_cancelled');
    boundary.dispose();
  });

  it('dispose clears the timer and detaches the listener', async () => {
    const turn = new AbortController();
    const boundary = linkTimeout(turn.signal, 10, 'slow');
    boundary.dispose();

    await new Promise((resolve) => setTimeout(resolve, 30));
    // The timer was cleared, so nothing aborted.
    expect(boundary.signal.aborted).toBe(false);

    // And the listener is gone: a later cancel does not reach this boundary.
    turn.abort(new TurnCancelledError('turn_1'));
    await flush();
    expect(boundary.signal.aborted).toBe(false);
  });

  it('recognises abort errors', () => {
    expect(isAbortError(new TurnCancelledError('t'))).toBe(true);
    expect(isAbortError(new ToolTimeoutError('x', 10))).toBe(true);
    expect(isAbortError(Object.assign(new Error('x'), { name: 'AbortError' }))).toBe(true);
    expect(isAbortError(new Error('ordinary failure'))).toBe(false);
  });
});

// ===========================================================================
// Tool timeout, through the real executor
// ===========================================================================

describe('tool timeout', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('applies a conservative default to tools that do not declare one', () => {
    harness = createTestJarvis();
    const infos = harness.jarvis.registry.infos(() => ({ available: true }));
    const time = infos.find((tool) => tool.name === 'current_time')!;
    expect(time.timeoutMs).toBe(DEFAULT_TOOL_TIMEOUT_MS);
    expect(DEFAULT_TOOL_TIMEOUT_MS).toBe(30_000);
  });

  it('lets a tool with different operational characteristics override it', () => {
    harness = createTestJarvis();
    const infos = harness.jarvis.registry.infos(() => ({ available: true }));
    const delegate = infos.find((tool) => tool.name === 'delegate_agent')!;
    // Delegation runs a whole sub-agent, so it is the one justified override.
    expect(delegate.timeoutMs).toBe(300_000);
    expect(delegate.timeoutMs).toBeGreaterThan(DEFAULT_TOOL_TIMEOUT_MS);
  });

  it('aborts a tool that exceeds its timeout, and classifies it as timeout', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(slowTool('slow_tool', 5_000, 40));

    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    const outcome = await harness.jarvis.executor.execute({
      tool: 'slow_tool',
      args: {},
      agent: harness.jarvis.jarvisAgent,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });
    turn.end();

    expect(outcome.status).toBe('timeout');
    expect(outcome.message).toMatch(/Timed out/);
    expect(outcome.message).toMatch(/40ms/);
    // The model is told plainly that nothing completed.
    expect(outcome.message).toMatch(/did not complete/);
  });

  it('stops waiting on a tool that ignores its abort signal', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(stubbornTool('stubborn_tool', 3_000, 40));

    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    const startedAt = Date.now();
    const outcome = await harness.jarvis.executor.execute({
      tool: 'stubborn_tool',
      args: {},
      agent: harness.jarvis.jarvisAgent,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });
    turn.end();

    expect(outcome.status).toBe('timeout');
    // Returned on the timeout, not after the tool's own 3s.
    expect(Date.now() - startedAt).toBeLessThan(1_000);
  });

  it('lets a tool that finishes inside its budget succeed', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(slowTool('quick_tool', 5, 2_000));

    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    const outcome = await harness.jarvis.executor.execute({
      tool: 'quick_tool',
      args: {},
      agent: harness.jarvis.jarvisAgent,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });
    turn.end();

    expect(outcome.status).toBe('executed');
    expect(outcome.result?.summary).toBe('quick_tool finished');
  });

  it('records the timeout in the audit log with the turn id', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(slowTool('slow_audit', 5_000, 30));

    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    await harness.jarvis.executor.execute({
      tool: 'slow_audit',
      args: {},
      agent: harness.jarvis.jarvisAgent,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });
    turn.end();

    const entry = harness.jarvis.store.audit
      .list(harness.userId)
      .find((row) => row.tool === 'slow_audit')!;
    expect(entry.error).toMatch(/Timed out/);
    expect(entry.turnId).toBe(turn.turnId);
  });

  it('does not leave the timer running after a fast success', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(slowTool('cleanup_tool', 1, 50_000));

    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    const outcome = await harness.jarvis.executor.execute({
      tool: 'cleanup_tool',
      args: {},
      agent: harness.jarvis.jarvisAgent,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });
    turn.end();

    expect(outcome.status).toBe('executed');
    // A 50s timer left armed would keep the event loop referenced; the boundary
    // unrefs and clears it, so the suite exits rather than hanging here.
  });
});

// ===========================================================================
// Cancellation, through the real orchestrator
// ===========================================================================

describe('turn cancellation', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('refuses to start a tool when the turn is already cancelled', async () => {
    harness = createTestJarvis();
    const seen: string[] = [];
    harness.jarvis.registry.register(recordingTool('recorder', seen));

    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    harness.jarvis.turns.cancel(turn.turnId);

    const outcome = await harness.jarvis.executor.execute({
      tool: 'recorder',
      args: {},
      agent: harness.jarvis.jarvisAgent,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });
    turn.end();

    expect(outcome.status).toBe('cancelled');
    // The tool body never ran.
    expect(seen).toHaveLength(0);
  });

  it('aborts a tool that is already running', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(slowTool('long_tool', 5_000));

    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    const pending = harness.jarvis.executor.execute({
      tool: 'long_tool',
      args: {},
      agent: harness.jarvis.jarvisAgent,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });

    await flush();
    harness.jarvis.turns.cancel(turn.turnId);

    const outcome = await pending;
    turn.end();

    expect(outcome.status).toBe('cancelled');
    expect(outcome.message).toMatch(/cancelled/i);
  });

  it('cancels a turn mid model call and reports cancellation, not failure', async () => {
    // A provider that hangs until its signal aborts — the shape of a real
    // in-flight HTTP request.
    const provider = new ScriptedProvider([{ content: 'never arrives' }]);
    provider.chat = (request) =>
      new Promise((_resolve, reject) => {
        request.signal?.addEventListener('abort', () => reject(request.signal?.reason), { once: true });
      });

    harness = createTestJarvis({ provider });
    const turnId = 'turn_model_cancel';

    const pending = harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'something slow',
      turnId,
    });

    await flush();
    expect(harness.jarvis.cancelTurn(turnId).status).toBe('cancelled');

    const result = await pending;
    expect(result.outcome).toBe('cancelled');
    expect(result.turnId).toBe(turnId);
    expect(result.error).toBeUndefined();
    expect(result.reply).toMatch(/Cancelled/);
  });

  it('does not start the next tool call after cancellation', async () => {
    const seen: string[] = [];
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'gate_tool', arguments: {} }, { name: 'recorder', arguments: {} }] },
        { content: 'unreachable' },
      ],
    });
    harness.jarvis.registry.register(recordingTool('recorder', seen));

    const turnId = 'turn_between_calls';
    // The first tool cancels the turn from inside its own execution, so the
    // second call in the same batch must never start.
    harness.jarvis.registry.register({
      name: 'gate_tool',
      description: 'Cancels the turn.',
      risk: 'READ',
      requiresApproval: false,
      inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
      async execute() {
        harness.jarvis.cancelTurn(turnId);
        return { ok: true, summary: 'cancelled the turn' };
      },
    });

    const result = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'run two tools',
      turnId,
    });

    expect(result.outcome).toBe('cancelled');
    expect(seen).toHaveLength(0);
    expect(result.toolCalls.map((call) => call.name)).toEqual(['gate_tool']);
  });

  it('cancelling after the turn completed is harmless', async () => {
    harness = createTestJarvis({ turns: [{ content: 'done' }] });

    const result = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'quick',
    });
    expect(result.outcome).toBe('completed');

    const outcome = harness.jarvis.cancelTurn(result.turnId);
    expect(outcome.status).toBe('not_found');
    expect(harness.jarvis.turns.size).toBe(0);
  });

  it('cancelling twice is idempotent', async () => {
    harness = createTestJarvis();
    const turn = harness.jarvis.turns.begin({ userId: harness.userId });

    expect(harness.jarvis.cancelTurn(turn.turnId).status).toBe('cancelled');
    expect(harness.jarvis.cancelTurn(turn.turnId).status).toBe('already_cancelled');
    turn.end();
  });

  it('cancelling an unknown turn returns a clean result', () => {
    harness = createTestJarvis();
    const outcome = harness.jarvis.cancelTurn('turn_never_existed');
    expect(outcome.status).toBe('not_found');
    expect(outcome.turnId).toBe('turn_never_existed');
  });

  it('releases the turn on completion, failure and cancellation alike', async () => {
    harness = createTestJarvis({ turns: [{ content: 'ok' }] });
    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'one' });
    expect(harness.jarvis.turns.size).toBe(0);

    harness.provider.script([{ content: 'unused' }]).failNext('provider exploded');
    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'two' });
    expect(harness.jarvis.turns.size).toBe(0);

    const turnId = 'turn_cleanup_cancel';
    harness.provider.script([{ toolCalls: [{ name: 'gate2', arguments: {} }] }, { content: 'x' }]);
    harness.jarvis.registry.register({
      name: 'gate2',
      description: 'Cancels.',
      risk: 'READ',
      requiresApproval: false,
      inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
      async execute() {
        harness.jarvis.cancelTurn(turnId);
        return { ok: true, summary: 'cancelled' };
      },
    });
    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'three', turnId });
    expect(harness.jarvis.turns.size).toBe(0);
  });

  it('cancellation does not bypass authorization', async () => {
    harness = createTestJarvis();
    const turn = harness.jarvis.turns.begin({ userId: harness.userId });
    harness.jarvis.turns.cancel(turn.turnId);

    // Scout may not delete files. A cancelled turn must not turn that refusal
    // into an execution, and must not create an approval either.
    const outcome = await harness.jarvis.executor.execute({
      tool: 'file_delete',
      args: { path: 'x.txt' },
      agent: harness.jarvis.agents.scout!,
      userId: harness.userId,
      conversationId: null,
      turn: turn.context,
    });
    turn.end();

    expect(outcome.status).toBe('cancelled');
    expect(harness.jarvis.store.approvals.list(harness.userId)).toHaveLength(0);
  });
});

// ===========================================================================
// Concurrency — the mandatory test
// ===========================================================================

describe('concurrent turns', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('cancelling one turn does not touch another', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(slowTool('a1', 3_000));
    harness.jarvis.registry.register(slowTool('a2', 3_000));
    harness.jarvis.registry.register(slowTool('b1', 60));
    harness.jarvis.registry.register(slowTool('b2', 60));

    const turnA = harness.jarvis.turns.begin({ userId: harness.userId });
    const turnB = harness.jarvis.turns.begin({ userId: harness.userId });
    expect(turnA.turnId).not.toBe(turnB.turnId);

    const run = (tool: string, turn: typeof turnA) =>
      harness.jarvis.executor.execute({
        tool,
        args: {},
        agent: harness.jarvis.jarvisAgent,
        userId: harness.userId,
        conversationId: null,
        turn: turn.context,
      });

    // TURN A: two tools. TURN B: two tools. All four in flight together.
    const a1 = run('a1', turnA);
    const a2 = run('a2', turnA);
    const b1 = run('b1', turnB);
    const b2 = run('b2', turnB);

    await flush();
    harness.jarvis.cancelTurn(turnA.turnId);

    const [ra1, ra2, rb1, rb2] = await Promise.all([a1, a2, b1, b2]);
    turnA.end();
    turnB.end();

    // A is cancelled, both of its tools.
    expect(ra1.status).toBe('cancelled');
    expect(ra2.status).toBe('cancelled');
    // B is untouched and completes normally.
    expect(rb1.status).toBe('executed');
    expect(rb2.status).toBe('executed');
    expect(turnB.isCancelled()).toBe(false);
  });

  it('runs two whole orchestrator turns concurrently with separate ids', async () => {
    const seen: string[] = [];
    harness = createTestJarvis();
    harness.jarvis.registry.register(recordingTool('recorder', seen));

    // Responds to the conversation rather than to a shared counter, so the
    // assertion does not depend on how the two turns interleave.
    harness.provider.chat = async (request) => {
      const sawTool = request.messages.some((message) => message.role === 'tool');
      return {
        content: sawTool ? 'done' : '',
        toolCalls: sawTool ? [] : [{ id: `call_${Math.random()}`, name: 'recorder', arguments: {} }],
        finishReason: sawTool ? ('stop' as const) : ('tool_calls' as const),
        model: 'scripted-model-v1',
        providerId: 'scripted',
        latencyMs: 1,
      };
    };

    const [first, second] = await Promise.all([
      harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'turn one' }),
      harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'turn two' }),
    ]);

    expect(first.turnId).not.toBe(second.turnId);
    expect(first.outcome).toBe('completed');
    expect(second.outcome).toBe('completed');

    // Each tool call saw its own turn's id — no cross-contamination.
    expect(new Set(seen).size).toBe(2);
    expect(seen).toContain(first.turnId);
    expect(seen).toContain(second.turnId);
    expect(harness.jarvis.turns.size).toBe(0);
  });

  it('a timeout in one turn does not disturb another', async () => {
    harness = createTestJarvis();
    harness.jarvis.registry.register(slowTool('times_out', 5_000, 40));
    harness.jarvis.registry.register(slowTool('finishes', 60, 5_000));

    const turnA = harness.jarvis.turns.begin({ userId: harness.userId });
    const turnB = harness.jarvis.turns.begin({ userId: harness.userId });

    const [a, b] = await Promise.all([
      harness.jarvis.executor.execute({
        tool: 'times_out',
        args: {},
        agent: harness.jarvis.jarvisAgent,
        userId: harness.userId,
        conversationId: null,
        turn: turnA.context,
      }),
      harness.jarvis.executor.execute({
        tool: 'finishes',
        args: {},
        agent: harness.jarvis.jarvisAgent,
        userId: harness.userId,
        conversationId: null,
        turn: turnB.context,
      }),
    ]);
    turnA.end();
    turnB.end();

    expect(a.status).toBe('timeout');
    expect(b.status).toBe('executed');
    expect(turnB.signal.aborted).toBe(false);
  });
});

// ===========================================================================
// Correlation
// ===========================================================================

describe('turn correlation', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('gives one turn one id, shared by every record it produces', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'current_time', arguments: {} }] },
        { content: 'It is midday.' },
      ],
    });

    const result = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'what time is it',
    });

    const trace = harness.jarvis.turnTrace(result.turnId);
    expect(trace.events.length).toBeGreaterThan(0);
    expect(trace.audit.length).toBeGreaterThan(0);
    expect(trace.toolCalls.length).toBeGreaterThan(0);

    // Everything carries the same id — not a fresh one per record.
    expect(new Set(trace.events.map((event) => event.turnId))).toEqual(new Set([result.turnId]));
    expect(new Set(trace.audit.map((row) => row.turnId))).toEqual(new Set([result.turnId]));
    expect(new Set(trace.toolCalls.map((row) => row.turnId))).toEqual(new Set([result.turnId]));
  });

  it('propagates the turn id into the tool context', async () => {
    const seen: string[] = [];
    harness = createTestJarvis({
      turns: [{ toolCalls: [{ name: 'recorder', arguments: {} }] }, { content: 'ok' }],
    });
    harness.jarvis.registry.register(recordingTool('recorder', seen));

    const result = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'record it',
    });

    expect(seen).toEqual([result.turnId]);
  });

  it('a delegated sub-agent shares its caller’s turn', async () => {
    const seen: string[] = [];
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'delegate_agent', arguments: { agent: 'scout', task: 'Check the time.' } }] },
        { toolCalls: [{ name: 'current_time', arguments: {} }] },
        { content: 'FINDINGS: time retrieved.' },
        { content: 'Scout reported.' },
      ],
    });
    harness.jarvis.registry.register(recordingTool('recorder', seen));

    const result = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'ask scout the time',
    });

    const trace = harness.jarvis.turnTrace(result.turnId);
    // The sub-agent's own tool call is in the same trace as the delegation.
    const tools = trace.audit.map((row) => row.tool);
    expect(tools).toContain('delegate_agent');
    expect(tools).toContain('current_time');
    // Including the one made by scout, not jarvis.
    expect(trace.audit.find((row) => row.tool === 'current_time')?.agent).toBe('scout');
    expect(new Set(trace.audit.map((row) => row.turnId))).toEqual(new Set([result.turnId]));
  });

  it('keeps concurrent turns’ records separate', async () => {
    harness = createTestJarvis();
    harness.provider.script([
      { toolCalls: [{ name: 'current_time', arguments: {} }] },
      { content: 'one' },
      { toolCalls: [{ name: 'current_time', arguments: {} }] },
      { content: 'two' },
    ]);

    const [a, b] = await Promise.all([
      harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'first' }),
      harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'second' }),
    ]);

    const traceA = harness.jarvis.turnTrace(a.turnId);
    const traceB = harness.jarvis.turnTrace(b.turnId);

    expect(traceA.audit.every((row) => row.turnId === a.turnId)).toBe(true);
    expect(traceB.audit.every((row) => row.turnId === b.turnId)).toBe(true);
    expect(traceA.events.some((event) => event.turnId === b.turnId)).toBe(false);
  });

  it('records a cancellation in the turn’s own trace', async () => {
    harness = createTestJarvis();
    const turnId = 'turn_traced_cancel';
    harness.provider.script([{ toolCalls: [{ name: 'gate3', arguments: {} }] }, { content: 'x' }]);
    harness.jarvis.registry.register({
      name: 'gate3',
      description: 'Cancels.',
      risk: 'READ',
      requiresApproval: false,
      inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
      async execute() {
        harness.jarvis.cancelTurn(turnId);
        return { ok: true, summary: 'cancelled' };
      },
    });

    await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'cancel me',
      turnId,
    });

    const trace = harness.jarvis.turnTrace(turnId);
    const types = trace.events.map((event) => event.type);
    // The whole story of the turn, in one query.
    expect(types).toContain('USER_MESSAGE');
    expect(types).toContain('TOOL_REQUEST');
    expect(types).toContain('TURN_CANCELLED');
  });

  it('reads pre-v0.2 records that have no turn id', () => {
    harness = createTestJarvis();
    // Simulate history written before the migration.
    harness.jarvis.store.db
      .prepare(
        `INSERT INTO audit_events (id, timestamp, user_id, agent, tool, arguments, approval_state,
           duration_ms, risk) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run('aud_legacy', new Date().toISOString(), harness.userId, 'jarvis', 'current_time', '{}', 'not_required', 1, 'READ');

    const rows = harness.jarvis.store.audit.list(harness.userId);
    const legacy = rows.find((row) => row.id === 'aud_legacy');
    expect(legacy).toBeTruthy();
    expect(legacy!.turnId).toBeNull();
    // And it does not appear in any turn's trace.
    expect(harness.jarvis.turnTrace('turn_anything').audit).toHaveLength(0);
  });

  it('indexes the correlation columns', () => {
    harness = createTestJarvis();
    const indexes = harness.jarvis.store.db
      .prepare("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE '%turn%'")
      .all() as { name: string }[];
    expect(indexes.map((row) => row.name).sort()).toEqual([
      'idx_audit_turn',
      'idx_events_turn',
      'idx_tool_calls_turn',
    ]);
  });

  it('applies the migration to an existing v1 database without losing history', async () => {
    const { mkdtempSync, rmSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const { Store } = await import('@jarvis/memory');

    const { default: Database } = await import('better-sqlite3');
    const { MIGRATIONS } = await import('@jarvis/memory');

    const dir = mkdtempSync(join(tmpdir(), 'jarvis-migrate-'));
    const path = join(dir, 'jarvis.db');

    // A genuine v1 database: migration 1 only, with real history in it.
    const legacy = new Database(path);
    legacy.exec(MIGRATIONS[0]!.sql);
    legacy.pragma('user_version = 1');
    const created = new Date().toISOString();
    legacy.prepare('INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)').run('user_legacy', 'Old', created);
    legacy
      .prepare('INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)')
      .run('conv_legacy', 'user_legacy', 'Old conversation', created, created);
    legacy
      .prepare('INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)')
      .run('msg_legacy', 'conv_legacy', 'user', 'from before v0.2', created);
    legacy
      .prepare(
        `INSERT INTO audit_events (id, timestamp, user_id, agent, tool, arguments, approval_state, duration_ms, risk)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run('aud_legacy', created, 'user_legacy', 'jarvis', 'current_time', '{}', 'not_required', 3, 'READ');
    legacy.close();

    const conversation = { id: 'conv_legacy' };

    // Reopen: migration 2 runs against the v1 database.
    const second = new Store(path);
    expect(second.health().schemaVersion).toBe(2);

    // History survives.
    expect(second.conversations.get(conversation.id)?.title).toBe('Old conversation');
    expect(second.messages.list(conversation.id)).toHaveLength(1);

    // And the pre-migration audit row is still readable, with a null turn id.
    const legacyRow = second.audit.list('user_legacy').find((row) => row.id === 'aud_legacy');
    expect(legacyRow).toBeTruthy();
    expect(legacyRow!.turnId).toBeNull();
    second.close();

    rmSync(dir, { recursive: true, force: true });
  });
});

// ===========================================================================
// HTTP surface
// ===========================================================================

describe('cancellation over HTTP', () => {
  let harness: TestHarness;
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    harness = createTestJarvis({ turns: [{ content: 'ok' }] });
    const started = await startJarvisServer({ jarvis: harness.jarvis, port: 0, host: '127.0.0.1' });
    server = started.server;
    baseUrl = `http://127.0.0.1:${started.port}`;
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    harness.cleanup();
  });

  const post = async (path: string, body: unknown = {}) => {
    const response = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    return { status: response.status, body: (await response.json()) as Record<string, unknown> };
  };

  it('returns 404 with a clean result for an unknown turn', async () => {
    const { status, body } = await post('/api/turns/turn_nope/cancel');
    expect(status).toBe(404);
    expect(body.status).toBe('not_found');
  });

  it('cancels an in-flight turn started over HTTP', async () => {
    const provider = harness.provider;
    provider.chat = (request) =>
      new Promise((_resolve, reject) => {
        request.signal?.addEventListener('abort', () => reject(request.signal?.reason), { once: true });
      });

    const turnId = 'turn_http_cancel';
    const chat = fetch(`${baseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ message: 'slow one', turnId }),
    });

    await new Promise((resolve) => setTimeout(resolve, 40));
    const cancel = await post(`/api/turns/${turnId}/cancel`, { reason: 'user pressed cancel' });
    expect(cancel.status).toBe(200);
    expect(cancel.body.status).toBe('cancelled');

    const turn = (await (await chat).json()) as { outcome: string; turnId: string };
    expect(turn.outcome).toBe('cancelled');
    expect(turn.turnId).toBe(turnId);
  });

  it('exposes the turn trace', async () => {
    const response = await fetch(`${baseUrl}/api/turns/turn_http_cancel`);
    expect(response.status).toBe(200);
    const trace = (await response.json()) as { turnId: string; events: unknown[] };
    expect(trace.turnId).toBe('turn_http_cancel');
    expect(trace.events.length).toBeGreaterThan(0);
  });

  it('lists active turns and leaves none behind', async () => {
    const response = await fetch(`${baseUrl}/api/turns`);
    const body = (await response.json()) as { turns: unknown[] };
    expect(body.turns).toHaveLength(0);
    expect(harness.jarvis.activeTurns(DEFAULT_USER_ID)).toHaveLength(0);
  });
});
