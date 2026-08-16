/**
 * Runtime state, telemetry and the surfaces the Control Room consumes.
 *
 * These assert that live state is computed in the runtime — not that a client
 * could compute it — and that everything reported is measured rather than
 * assumed.
 */
import { describe, it, expect, afterEach, beforeAll, afterAll } from 'vitest';
import type { Server } from 'node:http';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { EventBus } from '@jarvis/shared';
import { RuntimeMonitor, TelemetryCollector, classifyError } from '@jarvis/core';
import { startJarvisServer } from '@jarvis/server/src/server.ts';
import { DEFAULT_USER_ID } from '@jarvis/server/src/auth.ts';
import { createTestJarvis, type TestHarness } from './helpers.ts';

const event = (type: string, extra: Record<string, unknown> = {}) => ({
  id: `evt_${Math.random().toString(36).slice(2)}`,
  type: type as never,
  conversationId: 'conv_1',
  userId: 'user_test',
  agent: (extra.agent as string) ?? 'jarvis',
  summary: (extra.summary as string) ?? type,
  data: (extra.data as Record<string, unknown>) ?? {},
  createdAt: (extra.createdAt as string) ?? new Date().toISOString(),
});

describe('runtime monitor', () => {
  it('starts idle', () => {
    const monitor = new RuntimeMonitor();
    const snapshot = monitor.snapshot();
    expect(snapshot.phase).toBe('idle');
    expect(snapshot.busy).toBe(false);
    expect(snapshot.activeTools).toHaveLength(0);
    expect(snapshot.load).toBe(0);
  });

  it('tracks a tool execution from request to result', () => {
    const monitor = new RuntimeMonitor();
    monitor.ingest(event('USER_MESSAGE'));
    expect(monitor.snapshot().phase).toBe('thinking');

    monitor.ingest(event('TOOL_REQUEST', { data: { callId: 'exec_1', tool: 'file_read', risk: 'READ' } }));
    const busy = monitor.snapshot();
    expect(busy.phase).toBe('tool');
    expect(busy.busy).toBe(true);
    expect(busy.activeTools).toHaveLength(1);
    expect(busy.activeTools[0]!.tool).toBe('file_read');

    monitor.ingest(event('TOOL_RESULT', { data: { callId: 'exec_1', tool: 'file_read', ok: true } }));
    const settled = monitor.snapshot();
    expect(settled.activeTools).toHaveLength(0);
    expect(settled.phase).toBe('idle');
    expect(settled.counters.toolCalls).toBe(1);
    expect(settled.counters.toolFailures).toBe(0);
  });

  it('correlates concurrent calls to the same tool by call id', () => {
    const monitor = new RuntimeMonitor();
    monitor.ingest(event('TOOL_REQUEST', { data: { callId: 'a', tool: 'current_time' } }));
    monitor.ingest(event('TOOL_REQUEST', { data: { callId: 'b', tool: 'current_time' } }));
    expect(monitor.snapshot().activeTools).toHaveLength(2);

    monitor.ingest(event('TOOL_RESULT', { data: { callId: 'a', tool: 'current_time', ok: true } }));
    const remaining = monitor.snapshot().activeTools;
    expect(remaining).toHaveLength(1);
    expect(remaining[0]!.callId).toBe('b');
  });

  it('counts tool failures', () => {
    const monitor = new RuntimeMonitor();
    monitor.ingest(event('TOOL_REQUEST', { data: { callId: 'x', tool: 'file_read' } }));
    monitor.ingest(event('TOOL_RESULT', { data: { callId: 'x', tool: 'file_read', ok: false } }));
    expect(monitor.snapshot().counters.toolFailures).toBe(1);
  });

  it('tracks agent delegation', () => {
    const monitor = new RuntimeMonitor();
    monitor.ingest(event('AGENT_DELEGATION', { agent: 'scout' }));
    const running = monitor.snapshot();
    expect(running.phase).toBe('delegating');
    expect(running.activeAgents.map((run) => run.agent)).toEqual(['scout']);

    monitor.ingest(event('AGENT_RESULT', { agent: 'scout' }));
    expect(monitor.snapshot().activeAgents).toHaveLength(0);
    expect(monitor.snapshot().counters.agentRuns).toBe(1);
  });

  it('holds the awaiting-approval phase until the approval resolves', () => {
    const monitor = new RuntimeMonitor();
    monitor.ingest(event('TOOL_REQUEST', { data: { callId: 'z', tool: 'file_delete' } }));
    monitor.ingest(event('APPROVAL_REQUEST', { data: { approvalId: 'apr_1' } }));
    expect(monitor.snapshot().phase).toBe('awaiting_approval');

    // A tool result arriving first must not clear the approval state.
    monitor.ingest(event('TOOL_RESULT', { data: { callId: 'z', tool: 'file_delete', ok: true } }));
    expect(monitor.snapshot().phase).toBe('awaiting_approval');

    monitor.ingest(event('APPROVAL_RESOLVED', { data: { decision: 'approved' } }));
    expect(monitor.snapshot().phase).toBe('idle');
  });

  it('records the last error and classifies it', () => {
    const monitor = new RuntimeMonitor();
    monitor.ingest(event('ERROR', { data: { kind: 'provider' }, summary: 'HTTP 401' }));
    const snapshot = monitor.snapshot();
    expect(snapshot.phase).toBe('error');
    expect(snapshot.lastError?.kind).toBe('provider');
    expect(snapshot.lastError?.summary).toBe('HTTP 401');
  });

  it('classifies unconfigured-provider errors without an explicit tag', () => {
    expect(classifyError(event('ERROR', { data: { reason: 'nvidia: API key is not configured' } }))).toBe(
      'provider',
    );
    expect(classifyError(event('ERROR', { data: { tool: 'file_read' } }))).toBe('tool');
    expect(classifyError(event('ERROR', { data: {} }))).toBe('unknown');
  });

  it('clears a stale error when new work starts', () => {
    const monitor = new RuntimeMonitor();
    monitor.ingest(event('ERROR', { data: { kind: 'tool' } }));
    monitor.ingest(event('USER_MESSAGE'));
    expect(monitor.snapshot().lastError).toBeNull();
  });

  it('derives load from the real event rate, and it decays', () => {
    const monitor = new RuntimeMonitor();
    const base = Date.now();
    for (let i = 0; i < 12; i += 1) {
      monitor.ingest(event('TOOL_REQUEST', { data: { callId: `c${i}`, tool: 't' }, createdAt: new Date(base).toISOString() }));
    }
    expect(monitor.snapshot(base).load).toBe(1);
    // Same events, sampled a minute later: the window has moved on.
    expect(monitor.snapshot(base + 60_000).load).toBe(0);
  });

  it('takes the authoritative pending-approval count from the store', () => {
    const monitor = new RuntimeMonitor();
    monitor.syncPendingApprovals(2);
    expect(monitor.snapshot().phase).toBe('awaiting_approval');
    monitor.syncPendingApprovals(0);
    expect(monitor.snapshot().phase).toBe('idle');
  });

  it('survives a malformed event without throwing', () => {
    const monitor = new RuntimeMonitor();
    expect(() => monitor.ingest({ type: 'TOOL_REQUEST' } as never)).not.toThrow();
    expect(() => monitor.ingest(null as never)).not.toThrow();
  });

  it('attaches to a bus and observes emitted events', () => {
    const bus = new EventBus();
    const monitor = new RuntimeMonitor(bus);
    bus.emit({ type: 'USER_MESSAGE', userId: 'u', summary: 'hello' });
    expect(monitor.snapshot().counters.userMessages).toBe(1);
  });
});

describe('telemetry', () => {
  it('reports measured process and host values', () => {
    const collector = new TelemetryCollector(Date.now() - 5000);
    const sample = collector.sample();

    expect(sample.uptimeSeconds).toBeGreaterThanOrEqual(5);
    expect(sample.memory.processRssBytes).toBeGreaterThan(0);
    expect(sample.memory.processHeapUsedBytes).toBeGreaterThan(0);
    expect(sample.cpu.cores).toBeGreaterThan(0);
    expect(sample.cpu.processCpuSeconds).toBeGreaterThan(0);
    expect(sample.nodeVersion).toBe(process.version);
    expect(sample.pid).toBe(process.pid);
  });

  it('reports null for CPU fraction until it has two samples', () => {
    const collector = new TelemetryCollector();
    expect(collector.sample().cpu.processCpuFraction).toBeNull();
    const second = collector.sample(Date.now() + 100);
    expect(second.cpu.processCpuFraction).not.toBeNull();
    expect(second.cpu.processCpuFraction!).toBeGreaterThanOrEqual(0);
  });

  it('never reports a fabricated host memory figure', () => {
    const sample = new TelemetryCollector().sample();
    if (sample.memory.systemTotalBytes === null) {
      expect(sample.memory.systemUsedFraction).toBeNull();
    } else {
      expect(sample.memory.systemUsedFraction).toBeGreaterThan(0);
      expect(sample.memory.systemUsedFraction).toBeLessThanOrEqual(1);
    }
  });
});

describe('approval expiry', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('announces an expiry with an event and an audit row', async () => {
    harness = createTestJarvis({
      config: { approvalTimeoutSeconds: 0 },
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'gone.txt' } }] },
        { content: 'Awaiting approval.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'gone.txt'), 'x');

    const decisions: string[] = [];
    harness.jarvis.events.subscribe((event) => {
      if (event.type === 'APPROVAL_RESOLVED') decisions.push(String(event.data.decision));
    });

    // Expiry is triggered by the ordinary approval read inside the turn, not by
    // a sweep — and it is announced wherever it happens.
    await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete gone.txt',
    });

    expect(decisions).toContain('expired');

    const audit = harness.jarvis.store.audit
      .list(harness.userId)
      .filter((entry) => entry.approvalState === 'expired');
    expect(audit).toHaveLength(1);
    expect(audit[0]!.error).toMatch(/expired/);

    // The action never ran.
    expect(harness.jarvis.store.approvals.listPending(harness.userId)).toHaveLength(0);
  });

  it('announces each expiry exactly once, however many reads follow', async () => {
    harness = createTestJarvis({
      config: { approvalTimeoutSeconds: 0 },
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'a.txt' } }] },
        { content: 'Pending.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'a.txt'), 'x');

    let expiredEvents = 0;
    harness.jarvis.events.subscribe((event) => {
      if (event.type === 'APPROVAL_RESOLVED' && event.data.decision === 'expired') expiredEvents += 1;
    });

    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'delete a.txt' });
    expect(expiredEvents).toBe(1);

    // Further reads and an explicit sweep must not re-announce it.
    harness.jarvis.store.approvals.listPending(harness.userId);
    harness.jarvis.store.approvals.list(harness.userId);
    expect(harness.jarvis.sweepExpiredApprovals()).toHaveLength(0);
    expect(expiredEvents).toBe(1);
  });

  it('a request that has not timed out is left alone', async () => {
    harness = createTestJarvis({
      config: { approvalTimeoutSeconds: 900 },
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'keep.txt' } }] },
        { content: 'Pending.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'keep.txt'), 'x');

    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'delete keep.txt' });
    expect(harness.jarvis.sweepExpiredApprovals()).toHaveLength(0);
    expect(harness.jarvis.store.approvals.listPending(harness.userId)).toHaveLength(1);
  });
});

describe('runtime state snapshot', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('composes live state from the runtime, not from a client', async () => {
    harness = createTestJarvis({
      turns: [{ toolCalls: [{ name: 'current_time', arguments: {} }] }, { content: 'done' }],
    });
    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'time?' });

    const state = harness.jarvis.runtimeState(harness.userId);

    expect(state.version).toBe('0.1.0');
    expect(state.activity.counters.toolCalls).toBe(1);
    expect(state.activity.counters.userMessages).toBe(1);
    expect(state.agents.map((agent) => agent.name).sort()).toEqual([
      'advisor',
      'developer',
      'operator',
      'scout',
    ]);
    expect(state.agents.every((agent) => agent.running === false)).toBe(true);
    expect(state.tools.total).toBeGreaterThanOrEqual(12);
    expect(state.telemetry.uptimeSeconds).toBeGreaterThanOrEqual(0);
    expect(state.database.ok).toBe(true);
    expect(state.recentEvents.length).toBeGreaterThan(0);
    expect(state.approvals).toHaveLength(0);
  });

  it('reports pending approvals and the awaiting phase', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'x.txt' } }] },
        { content: 'Pending.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'x.txt'), 'x');
    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'delete x.txt' });

    const state = harness.jarvis.runtimeState(harness.userId);
    expect(state.approvals).toHaveLength(1);
    expect(state.approvals[0]!.risk).toBe('DESTRUCTIVE');
    expect(state.activity.phase).toBe('awaiting_approval');
    expect(state.counts.pendingApprovals).toBe(1);
  });

  it('reports provider availability honestly', () => {
    harness = createTestJarvis();
    const state = harness.jarvis.runtimeState(harness.userId);

    for (const provider of state.providers) {
      if (!provider.available) expect(provider.reason).toBeTruthy();
    }
    expect(state.media.image.available).toBe(false);
    expect(state.media.image.reason).toMatch(/IMAGE_PROVIDER/);
    expect(state.voice.stt.mode).toBe('browser');
  });

  it('contains no credential anywhere in the snapshot', () => {
    harness = createTestJarvis();
    harness.jarvis.config.nvidia.apiKey = 'nvapi-runtime-state-secret-1234';
    harness.jarvis.config.anthropic.apiKey = 'sk-ant-runtime-secret-5678';

    const serialised = JSON.stringify(harness.jarvis.runtimeState(harness.userId));
    expect(serialised).not.toContain('nvapi-runtime-state-secret-1234');
    expect(serialised).not.toContain('sk-ant-runtime-secret-5678');
    expect(serialised).not.toMatch(/apiKey|api_key/);
  });
});

describe('runtime HTTP surfaces', () => {
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

  it('serves GET /api/runtime/state', async () => {
    const response = await fetch(`${baseUrl}/api/runtime/state`);
    expect(response.status).toBe(200);
    const body = (await response.json()) as Record<string, unknown>;
    for (const key of ['activity', 'telemetry', 'providers', 'agents', 'approvals', 'tools', 'counts']) {
      expect(body, key).toHaveProperty(key);
    }
  });

  it('serves GET /api/system/telemetry with measured values', async () => {
    const response = await fetch(`${baseUrl}/api/system/telemetry`);
    expect(response.status).toBe(200);
    const body = (await response.json()) as { uptimeSeconds: number; memory: { processRssBytes: number } };
    expect(body.uptimeSeconds).toBeGreaterThanOrEqual(0);
    expect(body.memory.processRssBytes).toBeGreaterThan(0);
  });

  it('sends a state frame first on the SSE stream', async () => {
    const controller = new AbortController();
    const response = await fetch(`${baseUrl}/api/events/stream`, { signal: controller.signal });
    expect(response.status).toBe(200);

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    // Read until the first complete frame arrives.
    for (let i = 0; i < 5 && !buffer.includes('event: state'); i += 1) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value);
    }

    expect(buffer).toContain('event: state');
    const dataLine = buffer.split('\n').find((line) => line.startsWith('data: '));
    expect(dataLine).toBeTruthy();
    const snapshot = JSON.parse(dataLine!.slice(6)) as { activity: unknown; telemetry: unknown };
    expect(snapshot.activity).toBeTruthy();
    expect(snapshot.telemetry).toBeTruthy();

    controller.abort();
    await reader.cancel().catch(() => {});
  });

  it('emits a tool call-id that correlates request with result', async () => {
    harness.provider.script([
      { toolCalls: [{ name: 'current_time', arguments: {} }] },
      { content: 'done' },
    ]);
    await fetch(`${baseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ message: 'time?' }),
    });

    // Requests over HTTP authenticate as the API user, not the harness user.
    const events = harness.jarvis.store.events.list(DEFAULT_USER_ID, { limit: 50 });
    const request = events.find((event) => event.type === 'TOOL_REQUEST');
    const result = events.find((event) => event.type === 'TOOL_RESULT');
    expect(request?.data.callId).toBeTruthy();
    expect(result?.data.callId).toBe(request?.data.callId);
  });

  it('emits MEMORY_READ when memory is searched', async () => {
    harness.provider.script([
      { toolCalls: [{ name: 'memory_search', arguments: { query: 'anything at all' } }] },
      { content: 'nothing found' },
    ]);
    await fetch(`${baseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ message: 'what do you know' }),
    });

    const events = harness.jarvis.store.events.list(DEFAULT_USER_ID, { limit: 30 });
    const read = events.find((event) => event.type === 'MEMORY_READ' && event.data.via === 'memory_search');
    expect(read).toBeTruthy();
  });
});
