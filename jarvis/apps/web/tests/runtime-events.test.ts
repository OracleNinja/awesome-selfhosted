/**
 * Event normalisation and runtime state projection.
 *
 * These cover the translation layer between the runtime's event names and the
 * semantic names the Control Room renders — and prove the projection stays a
 * projection: nothing here decides anything the runtime has not already decided.
 */
import { describe, it, expect } from 'vitest';
import { classify, normalizeEvent, isStale, STALE_EVENT_MS } from '../src/runtime/events';
import { RuntimeStore, coreVisualState, coreIntensity, connectionLabel } from '../src/runtime/state';
import type { JarvisEvent, RuntimeStateSnapshot } from '../src/runtime/types';

const event = (type: string, data: Record<string, unknown> = {}, agent = 'jarvis'): JarvisEvent => ({
  id: `evt_${Math.random().toString(36).slice(2)}`,
  type: type as JarvisEvent['type'],
  conversationId: 'conv_1',
  userId: 'user_local',
  agent,
  summary: `${type} summary`,
  data,
  createdAt: new Date().toISOString(),
});

describe('event classification', () => {
  it('maps every runtime event type to a semantic kind', () => {
    expect(classify(event('USER_MESSAGE'))).toBe('orchestration');
    expect(classify(event('MODEL_RESPONSE'))).toBe('orchestration');
    expect(classify(event('ACTION_EXECUTED'))).toBe('orchestration');
    expect(classify(event('AGENT_DELEGATION', {}, 'scout'))).toBe('agent.started');
    expect(classify(event('AGENT_RESULT', {}, 'scout'))).toBe('agent.completed');
    expect(classify(event('TOOL_REQUEST', { tool: 'x' }))).toBe('tool.requested');
    expect(classify(event('TOOL_RESULT', { tool: 'x', ok: true }))).toBe('tool.completed');
    expect(classify(event('TOOL_RESULT', { tool: 'x', ok: false }))).toBe('tool.failed');
    expect(classify(event('APPROVAL_REQUEST'))).toBe('approval.requested');
    expect(classify(event('MEMORY_WRITE'))).toBe('memory.write');
    expect(classify(event('MEMORY_READ'))).toBe('memory.read');
  });

  it('distinguishes the three approval outcomes', () => {
    expect(classify(event('APPROVAL_RESOLVED', { decision: 'approved' }))).toBe('approval.approved');
    expect(classify(event('APPROVAL_RESOLVED', { decision: 'denied' }))).toBe('approval.denied');
    expect(classify(event('APPROVAL_RESOLVED', { decision: 'expired' }))).toBe('approval.expired');
  });

  it('separates memory search from plain retrieval', () => {
    expect(classify(event('MEMORY_READ', { via: 'memory_search' }))).toBe('memory.search');
  });

  it('separates provider failures from agent and tool failures', () => {
    expect(classify(event('ERROR', { kind: 'provider' }))).toBe('provider.error');
    expect(classify(event('ERROR', {}, 'scout'))).toBe('agent.failed');
    expect(classify(event('ERROR', {}, 'jarvis'))).toBe('error');
  });
});

describe('event normalisation', () => {
  it('normalises a well-formed event', () => {
    const normalized = normalizeEvent(event('TOOL_REQUEST', { tool: 'current_time' }));
    expect(normalized).not.toBeNull();
    expect(normalized!.kind).toBe('tool.requested');
    expect(normalized!.data.tool).toBe('current_time');
    expect(normalized!.receivedAt).toBeGreaterThan(0);
  });

  it('rejects malformed payloads instead of throwing', () => {
    for (const bad of [null, undefined, 'a string', 42, [], {}, { id: 'x' }, { type: 'TOOL_REQUEST' }]) {
      expect(normalizeEvent(bad), JSON.stringify(bad)).toBeNull();
    }
  });

  it('keeps an unknown event type rather than dropping the event', () => {
    const normalized = normalizeEvent({
      id: 'evt_future',
      type: 'QUANTUM_ENTANGLED',
      summary: 'from a newer runtime',
      createdAt: new Date().toISOString(),
    });
    expect(normalized).not.toBeNull();
    expect(normalized!.kind).toBe('unknown');
    expect(normalized!.type).toBe('QUANTUM_ENTANGLED');
  });

  it('repairs missing optional fields', () => {
    const normalized = normalizeEvent({ id: 'e1', type: 'ERROR', summary: 'boom' });
    expect(normalized!.agent).toBe('unknown');
    expect(normalized!.conversationId).toBeNull();
    expect(normalized!.data).toEqual({});
    expect(Number.isNaN(Date.parse(normalized!.createdAt))).toBe(false);
  });

  it('detects stale events', () => {
    const old = normalizeEvent({
      id: 'e_old',
      type: 'USER_MESSAGE',
      summary: 'ancient',
      createdAt: new Date(Date.now() - STALE_EVENT_MS - 1000).toISOString(),
    })!;
    expect(isStale(old)).toBe(true);
    expect(isStale(normalizeEvent(event('USER_MESSAGE'))!)).toBe(false);
  });
});

describe('runtime store', () => {
  it('folds a tool execution into tool activity', () => {
    const store = new RuntimeStore();
    store.ingestRaw(event('TOOL_REQUEST', { callId: 'c1', tool: 'file_read', risk: 'READ' }));

    let activity = store.getState().toolActivity;
    expect(activity).toHaveLength(1);
    expect(activity[0]!.status).toBe('running');
    expect(activity[0]!.risk).toBe('READ');

    store.ingestRaw(event('TOOL_RESULT', { callId: 'c1', tool: 'file_read', ok: true, durationMs: 12 }));
    activity = store.getState().toolActivity;
    expect(activity).toHaveLength(1);
    expect(activity[0]!.status).toBe('completed');
    expect(activity[0]!.durationMs).toBe(12);
  });

  it('marks a failed tool execution', () => {
    const store = new RuntimeStore();
    store.ingestRaw(event('TOOL_REQUEST', { callId: 'c2', tool: 'file_read' }));
    store.ingestRaw(
      event('TOOL_RESULT', { callId: 'c2', tool: 'file_read', ok: false, error: 'file not found' }),
    );
    const entry = store.getState().toolActivity[0]!;
    expect(entry.status).toBe('failed');
    expect(entry.error).toBe('file not found');
  });

  it('marks a tool execution as awaiting approval', () => {
    const store = new RuntimeStore();
    store.ingestRaw(event('TOOL_REQUEST', { callId: 'c3', tool: 'file_delete', risk: 'DESTRUCTIVE' }));
    store.ingestRaw(event('APPROVAL_REQUEST', { callId: 'c3', tool: 'file_delete' }));
    expect(store.getState().toolActivity[0]!.status).toBe('awaiting_approval');
  });

  it('keeps concurrent calls to one tool separate', () => {
    const store = new RuntimeStore();
    store.ingestRaw(event('TOOL_REQUEST', { callId: 'a', tool: 'current_time' }));
    store.ingestRaw(event('TOOL_REQUEST', { callId: 'b', tool: 'current_time' }));
    store.ingestRaw(event('TOOL_RESULT', { callId: 'a', tool: 'current_time', ok: true }));

    const activity = store.getState().toolActivity;
    expect(activity.find((entry) => entry.callId === 'a')!.status).toBe('completed');
    expect(activity.find((entry) => entry.callId === 'b')!.status).toBe('running');
  });

  it('records memory activity without exposing memory contents beyond the runtime summary', () => {
    const store = new RuntimeStore();
    store.ingestRaw(event('MEMORY_WRITE', { id: 'mem_1' }));
    store.ingestRaw(event('MEMORY_READ', { via: 'memory_search', matches: 2 }));

    const activity = store.getState().memoryActivity;
    expect(activity.map((entry) => entry.kind)).toEqual(['search', 'write']);
    // Only the runtime's own (already redacted) summary is carried.
    expect(Object.keys(activity[0]!)).toEqual(['id', 'kind', 'summary', 'agent', 'at']);
  });

  it('counts malformed frames instead of throwing', () => {
    const store = new RuntimeStore();
    expect(store.ingestRaw('not an event')).toBe(false);
    expect(store.ingestRaw(null)).toBe(false);
    expect(store.getState().droppedEvents).toBe(2);
    expect(store.getState().recentEvents).toHaveLength(0);
  });

  it('ignores a duplicate event id', () => {
    const store = new RuntimeStore();
    const one = event('USER_MESSAGE');
    store.ingestRaw(one);
    store.ingestRaw(one);
    expect(store.getState().recentEvents).toHaveLength(1);
  });

  it('notifies subscribers and can be unsubscribed', () => {
    const store = new RuntimeStore();
    let calls = 0;
    const unsubscribe = store.subscribe(() => {
      calls += 1;
    });
    store.ingestRaw(event('USER_MESSAGE'));
    expect(calls).toBe(1);
    unsubscribe();
    store.ingestRaw(event('MODEL_RESPONSE'));
    expect(calls).toBe(1);
  });

  it('survives a throwing subscriber', () => {
    const store = new RuntimeStore();
    store.subscribe(() => {
      throw new Error('bad subscriber');
    });
    expect(() => store.ingestRaw(event('USER_MESSAGE'))).not.toThrow();
    expect(store.getState().recentEvents).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------

export function snapshotFixture(overrides: Partial<RuntimeStateSnapshot> = {}): RuntimeStateSnapshot {
  return {
    version: '0.1.0',
    sampledAt: new Date().toISOString(),
    activity: {
      phase: 'idle',
      busy: false,
      activeAgents: [],
      activeTools: [],
      lastError: null,
      counters: {
        events: 0,
        userMessages: 0,
        modelResponses: 0,
        toolCalls: 0,
        toolFailures: 0,
        agentRuns: 0,
        approvalsRequested: 0,
        approvalsResolved: 0,
        memoryWrites: 0,
        memoryReads: 0,
        errors: 0,
      },
      lastEventAt: null,
      load: 0,
    },
    telemetry: {
      uptimeSeconds: 42,
      startedAt: new Date().toISOString(),
      sampledAt: new Date().toISOString(),
      nodeVersion: 'v22.0.0',
      platform: 'linux',
      hostname: 'test',
      pid: 1,
      memory: {
        processRssBytes: 1024,
        processHeapUsedBytes: 512,
        processHeapTotalBytes: 1024,
        systemTotalBytes: 8192,
        systemFreeBytes: 4096,
        systemUsedFraction: 0.5,
      },
      cpu: {
        processCpuSeconds: 1,
        processCpuFraction: 0.1,
        cores: 4,
        loadAverage1m: 0.5,
        loadPerCore: 0.125,
      },
    },
    providers: [],
    activeModel: { provider: 'nvidia', model: 'meta/llama-3.3-70b-instruct', available: true },
    voice: {
      stt: { id: 'browser:stt', kind: 'stt', available: true, mode: 'browser' },
      tts: { id: 'browser:tts', kind: 'tts', available: true, mode: 'browser' },
    },
    media: {
      image: { id: 'image:none', kind: 'image', available: false, reason: 'not configured' },
      imageEdit: { id: 'image_edit:none', kind: 'image_edit', available: false, reason: 'not configured' },
      video: { id: 'video:none', kind: 'video', available: false, reason: 'not configured' },
      vision: { id: 'vision:none', kind: 'vision', available: false, reason: 'not configured' },
    },
    agents: [],
    approvals: [],
    tools: { total: 13, requiringApproval: 3, names: [] },
    database: { ok: true, schemaVersion: 1, tables: [] },
    counts: { memories: 0, auditEvents: 0, pendingApprovals: 0, conversations: 0 },
    recentEvents: [],
    charterErrors: [],
    ...overrides,
  };
}

export const approvalFixture = {
  id: 'apr_1',
  userId: 'user_local',
  conversationId: 'conv_1',
  agent: 'jarvis',
  tool: 'file_delete',
  description: 'file_delete(path="x.txt")',
  risk: 'DESTRUCTIVE' as const,
  arguments: { path: 'x.txt' },
  state: 'pending' as const,
  createdAt: new Date().toISOString(),
  resolvedAt: null,
  expiresAt: new Date(Date.now() + 900_000).toISOString(),
  decidedBy: null,
  note: null,
};

describe('core visual state', () => {
  const withState = (mutate: (store: RuntimeStore) => void) => {
    const store = new RuntimeStore();
    store.setConnection('online', 'connected');
    store.applySnapshot(snapshotFixture());
    mutate(store);
    return store.getState();
  };

  it('is offline before the runtime is reachable', () => {
    const store = new RuntimeStore();
    store.setConnection('offline', 'no runtime');
    expect(coreVisualState(store.getState())).toBe('offline');
    expect(coreIntensity(store.getState())).toBe(0);
  });

  it('is offline when the token is rejected', () => {
    const store = new RuntimeStore();
    store.setConnection('unauthorized', 'bad token');
    expect(coreVisualState(store.getState())).toBe('offline');
  });

  it('is idle on a connected but quiet runtime', () => {
    const state = withState(() => {});
    expect(coreVisualState(state)).toBe('idle');
    expect(coreIntensity(state)).toBeLessThan(0.2);
  });

  it('is processing while the runtime is busy', () => {
    const state = withState((store) =>
      store.applySnapshot(
        snapshotFixture({
          activity: { ...snapshotFixture().activity, phase: 'tool', busy: true, load: 0.3 },
        }),
      ),
    );
    expect(coreVisualState(state)).toBe('processing');
  });

  it('is high_load when the real event rate saturates', () => {
    const state = withState((store) =>
      store.applySnapshot(
        snapshotFixture({
          activity: { ...snapshotFixture().activity, phase: 'tool', busy: true, load: 0.9 },
        }),
      ),
    );
    expect(coreVisualState(state)).toBe('high_load');
    expect(coreIntensity(state)).toBeGreaterThan(0.8);
  });

  it('shows approval_required whenever an approval is pending', () => {
    const state = withState((store) =>
      store.applySnapshot(snapshotFixture({ approvals: [approvalFixture] })),
    );
    expect(coreVisualState(state)).toBe('approval_required');
  });

  it('shows the error state when the runtime reports one', () => {
    const state = withState((store) =>
      store.applySnapshot(
        snapshotFixture({
          activity: {
            ...snapshotFixture().activity,
            phase: 'error',
            lastError: { summary: 'HTTP 401', agent: 'jarvis', kind: 'provider', at: new Date().toISOString() },
          },
        }),
      ),
    );
    expect(coreVisualState(state)).toBe('error');
  });

  it('reflects voice phases, which outrank background activity', () => {
    const listening = withState((store) => store.setVoicePhase('listening'));
    expect(coreVisualState(listening)).toBe('listening');

    const speaking = withState((store) => store.setVoicePhase('speaking'));
    expect(coreVisualState(speaking)).toBe('speaking');

    const processing = withState((store) => store.setVoicePhase('processing'));
    expect(coreVisualState(processing)).toBe('processing');

    const idle = withState((store) => store.setVoicePhase('idle'));
    expect(coreVisualState(idle)).toBe('idle');
  });

  it('never invents activity: an idle runtime yields a near-zero intensity', () => {
    const state = withState(() => {});
    // No timer, no floor beyond a faint idle shimmer.
    expect(coreIntensity(state)).toBeLessThanOrEqual(0.1);
  });
});

describe('connection labelling', () => {
  it('reports ONLINE, OFFLINE, CONNECTING and UNAUTHORIZED distinctly', () => {
    const store = new RuntimeStore();
    expect(connectionLabel(store.getState()).label).toBe('CONNECTING');

    store.setConnection('online', 'ok');
    store.applySnapshot(snapshotFixture());
    expect(connectionLabel(store.getState()).label).toBe('ONLINE');

    store.setConnection('offline', 'gone');
    expect(connectionLabel(store.getState()).label).toBe('OFFLINE');

    store.setConnection('unauthorized', 'bad token');
    expect(connectionLabel(store.getState()).label).toBe('UNAUTHORIZED');
  });

  it('reports DEGRADED when connected but the model provider is unavailable', () => {
    const store = new RuntimeStore();
    store.setConnection('online', 'ok');
    store.applySnapshot(
      snapshotFixture({
        activeModel: {
          provider: 'nvidia',
          model: 'meta/llama-3.3-70b-instruct',
          available: false,
          reason: 'API key is not configured',
        },
      }),
    );
    expect(connectionLabel(store.getState()).label).toBe('DEGRADED');
  });
});
