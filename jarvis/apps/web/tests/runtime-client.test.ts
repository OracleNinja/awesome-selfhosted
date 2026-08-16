/**
 * The runtime client: connection lifecycle, reconnection, and command paths.
 *
 * `fetch` and `EventSource` are injected, so every failure mode the brief lists
 * — disconnect, reconnect, malformed frames, unknown event types, backend
 * failure on approval — is exercised deterministically without a browser and
 * without waiting on real timers.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { JarvisRuntimeClient, BRIEFING_PROMPT, type EventSourceLike } from '../src/runtime/client';
import { snapshotFixture, approvalFixture } from './runtime-events.test';

/** A controllable stand-in for the browser's EventSource. */
class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  onerror: ((event: unknown) => void) | null = null;
  onopen: ((event: unknown) => void) | null = null;
  private listeners = new Map<string, ((event: { data: string }) => void)[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: { data: string }) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  close(): void {
    this.closed = true;
  }

  /** Deliver a frame as the server would. */
  emit(type: string, payload: unknown): void {
    const data = typeof payload === 'string' ? payload : JSON.stringify(payload);
    for (const listener of this.listeners.get(type) ?? []) listener({ data });
  }

  open(): void {
    this.onopen?.({});
  }

  fail(): void {
    this.onerror?.({});
  }

  static latest(): FakeEventSource {
    const instance = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    if (!instance) throw new Error('no EventSource was created');
    return instance;
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }
}

interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

/** Records requests and replies from a route table. */
function stubFetch(routes: Record<string, (call: FetchCall) => { status?: number; body: unknown }>) {
  const calls: FetchCall[] = [];
  const impl = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const call: FetchCall = {
      url,
      method: init?.method ?? 'GET',
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);

    const key = Object.keys(routes).find((route) => url.startsWith(route));
    if (!key) return new Response(JSON.stringify({ error: { code: 'not_found', message: url } }), { status: 404 });

    const { status = 200, body } = routes[key]!(call);
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'content-type': 'application/json' },
    });
  });

  vi.stubGlobal('fetch', impl);
  return calls;
}

const scheduled: (() => void)[] = [];
const fakeSetTimeout = (fn: () => void) => {
  scheduled.push(fn);
  return scheduled.length;
};
const runScheduled = async () => {
  const pending = [...scheduled];
  scheduled.length = 0;
  for (const fn of pending) await fn();
  // The scheduled callback kicks off an async reconnect it does not await;
  // let its promise chain settle before asserting.
  await new Promise((resolve) => setTimeout(resolve, 0));
};

function makeClient() {
  return new JarvisRuntimeClient({
    eventSourceFactory: (url) => new FakeEventSource(url),
    setTimeoutImpl: fakeSetTimeout,
    clearTimeoutImpl: () => undefined,
    reconnectDelays: [1000, 2000],
    tokenProvider: () => '',
  });
}

beforeEach(() => {
  FakeEventSource.reset();
  scheduled.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('connection lifecycle', () => {
  it('fetches a snapshot then opens exactly one stream', async () => {
    stubFetch({ '/api/runtime/state': () => ({ body: snapshotFixture() }) });
    const client = makeClient();

    await client.connect();

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.latest().url).toBe('/api/events/stream');
    expect(client.store.getState().connection).toBe('online');
    expect(client.store.getState().snapshot?.version).toBe('0.1.0');
  });

  it('passes the session token on the stream URL when one is set', async () => {
    stubFetch({ '/api/runtime/state': () => ({ body: snapshotFixture() }) });
    const client = new JarvisRuntimeClient({
      eventSourceFactory: (url) => new FakeEventSource(url),
      setTimeoutImpl: fakeSetTimeout,
      clearTimeoutImpl: () => undefined,
      tokenProvider: () => 'secret-token',
    });

    await client.connect();
    expect(FakeEventSource.latest().url).toBe('/api/events/stream?token=secret-token');
  });

  it('reports unauthorized distinctly from offline', async () => {
    stubFetch({
      '/api/runtime/state': () => ({ status: 401, body: { error: { code: 'unauthorized', message: 'nope' } } }),
    });
    const client = makeClient();
    await client.connect();

    expect(client.store.getState().connection).toBe('unauthorized');
    expect(client.store.getState().connectionDetail).toMatch(/token/i);
  });

  it('reports offline when the runtime is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));
    const client = makeClient();
    await client.connect();

    expect(client.store.getState().connection).toBe('offline');
    expect(client.store.getState().connectionDetail).toMatch(/ECONNREFUSED/);
  });

  it('closes the stream and stops on disconnect', async () => {
    stubFetch({ '/api/runtime/state': () => ({ body: snapshotFixture() }) });
    const client = makeClient();
    await client.connect();

    const source = FakeEventSource.latest();
    client.disconnect();

    expect(source.closed).toBe(true);
    expect(client.store.getState().connection).toBe('offline');

    // A late failure on the closed stream must not schedule a reconnect.
    source.fail();
    expect(scheduled).toHaveLength(0);
  });
});

describe('reconnection', () => {
  it('reconnects with backoff and resynchronises from a fresh snapshot', async () => {
    let snapshots = 0;
    stubFetch({
      '/api/runtime/state': () => {
        snapshots += 1;
        return { body: snapshotFixture({ counts: { memories: snapshots, auditEvents: 0, pendingApprovals: 0, conversations: 0 } }) };
      },
    });
    const client = makeClient();
    await client.connect();
    expect(snapshots).toBe(1);

    FakeEventSource.latest().fail();
    expect(client.store.getState().connection).toBe('offline');
    expect(client.store.getState().reconnectAttempts).toBe(1);
    expect(client.store.getState().connectionDetail).toMatch(/Reconnecting in 1s/);
    expect(scheduled).toHaveLength(1);

    await runScheduled();

    // A new stream, and state re-read rather than inferred.
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(snapshots).toBe(2);
    expect(client.store.getState().connection).toBe('online');
    expect(client.store.getState().snapshot?.counts.memories).toBe(2);
  });

  it('lengthens the delay while reconnects keep failing', async () => {
    // The runtime stays unreachable, so each attempt escalates.
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('still down'); }));
    const client = makeClient();
    await client.connect();

    FakeEventSource.latest().fail();
    expect(client.store.getState().connectionDetail).toMatch(/in 1s \(attempt 1\)/);
    await runScheduled();

    FakeEventSource.latest().fail();
    expect(client.store.getState().connectionDetail).toMatch(/in 2s \(attempt 2\)/);
    await runScheduled();

    // The schedule is capped at its last entry rather than growing forever.
    FakeEventSource.latest().fail();
    expect(client.store.getState().connectionDetail).toMatch(/in 2s \(attempt 3\)/);
  });

  it('resets the attempt counter once reconnected', async () => {
    stubFetch({ '/api/runtime/state': () => ({ body: snapshotFixture() }) });
    const client = makeClient();
    await client.connect();

    FakeEventSource.latest().fail();
    expect(client.store.getState().reconnectAttempts).toBe(1);
    await runScheduled();
    FakeEventSource.latest().open();

    expect(client.store.getState().reconnectAttempts).toBe(0);
    expect(client.store.getState().connection).toBe('online');
  });
});

describe('stream frames', () => {
  const connect = async () => {
    stubFetch({
      '/api/runtime/state': () => ({ body: snapshotFixture() }),
      '/api/approvals': () => ({ body: { approvals: [approvalFixture] } }),
    });
    const client = makeClient();
    await client.connect();
    return client;
  };

  it('applies a state frame', async () => {
    const client = await connect();
    FakeEventSource.latest().emit('state', snapshotFixture({ version: '0.2.0' }));
    expect(client.store.getState().snapshot?.version).toBe('0.2.0');
    expect(client.store.getState().connection).toBe('online');
  });

  it('ingests a jarvis event and updates runtime state', async () => {
    const client = await connect();
    FakeEventSource.latest().emit('jarvis', {
      id: 'evt_1',
      type: 'TOOL_REQUEST',
      summary: 'jarvis → current_time',
      agent: 'jarvis',
      conversationId: 'conv_1',
      userId: 'user_local',
      data: { callId: 'c1', tool: 'current_time', risk: 'READ' },
      createdAt: new Date().toISOString(),
    });

    const state = client.store.getState();
    expect(state.recentEvents[0]!.kind).toBe('tool.requested');
    expect(state.toolActivity[0]!.tool).toBe('current_time');
  });

  it('applies a telemetry frame without touching the event log', async () => {
    const client = await connect();
    const before = client.store.getState().recentEvents.length;

    FakeEventSource.latest().emit('telemetry', {
      telemetry: { ...snapshotFixture().telemetry, uptimeSeconds: 999 },
      activity: { ...snapshotFixture().activity, load: 0.6 },
    });

    expect(client.store.getState().telemetry?.uptimeSeconds).toBe(999);
    expect(client.store.getState().activity?.load).toBe(0.6);
    expect(client.store.getState().recentEvents).toHaveLength(before);
  });

  it('counts malformed frames and keeps the subscription alive', async () => {
    const client = await connect();
    const source = FakeEventSource.latest();

    source.emit('jarvis', 'this is not json{');
    source.emit('jarvis', { nonsense: true });
    expect(client.store.getState().droppedEvents).toBe(2);

    // Still working afterwards.
    source.emit('jarvis', {
      id: 'evt_ok',
      type: 'USER_MESSAGE',
      summary: 'still alive',
      createdAt: new Date().toISOString(),
    });
    expect(client.store.getState().recentEvents[0]!.summary).toBe('still alive');
  });

  it('keeps an unknown event type instead of dropping it', async () => {
    const client = await connect();
    FakeEventSource.latest().emit('jarvis', {
      id: 'evt_new',
      type: 'SOMETHING_NEW',
      summary: 'from a newer runtime',
      createdAt: new Date().toISOString(),
    });

    const event = client.store.getState().recentEvents[0]!;
    expect(event.kind).toBe('unknown');
    expect(client.store.getState().droppedEvents).toBe(0);
  });

  it('re-reads approvals from the runtime when an approval event arrives', async () => {
    const client = await connect();
    FakeEventSource.latest().emit('jarvis', {
      id: 'evt_apr',
      type: 'APPROVAL_REQUEST',
      summary: 'Approval required',
      agent: 'jarvis',
      data: { approvalId: 'apr_1', tool: 'file_delete' },
      createdAt: new Date().toISOString(),
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(client.store.getState().approvals).toHaveLength(1);
    expect(client.store.getState().approvals[0]!.tool).toBe('file_delete');
  });
});

describe('commands', () => {
  it('sends a command through /api/chat and takes approvals from the response', async () => {
    const calls = stubFetch({
      '/api/runtime/state': () => ({ body: snapshotFixture() }),
      '/api/chat': () => ({
        body: {
          conversationId: 'conv_9',
          reply: 'Acknowledged.',
          messages: [],
          toolCalls: [],
          pendingApprovals: [approvalFixture],
          events: [],
          model: 'm',
          provider: 'nvidia',
          iterations: 1,
        },
      }),
    });
    const client = makeClient();
    await client.connect();

    const result = await client.sendCommand('status report');

    expect(result.ok).toBe(true);
    expect(result.conversationId).toBe('conv_9');
    const chat = calls.find((call) => call.url.startsWith('/api/chat'))!;
    expect(chat.method).toBe('POST');
    expect((chat.body as { message: string }).message).toBe('status report');
    expect(client.store.getState().approvals).toHaveLength(1);
  });

  it('BRIEF ME goes through the same runtime turn, not a separate path', async () => {
    const calls = stubFetch({
      '/api/runtime/state': () => ({ body: snapshotFixture() }),
      '/api/chat': () => ({
        body: {
          conversationId: 'conv_brief',
          reply: 'Brief.',
          messages: [],
          toolCalls: [],
          pendingApprovals: [],
          events: [],
          model: 'm',
          provider: 'nvidia',
          iterations: 1,
        },
      }),
    });
    const client = makeClient();
    await client.connect();

    await client.brief();

    const chat = calls.find((call) => call.url.startsWith('/api/chat'))!;
    expect(chat.method).toBe('POST');
    expect((chat.body as { message: string }).message).toBe(BRIEFING_PROMPT);
  });

  it('surfaces a command failure rather than pretending it worked', async () => {
    stubFetch({
      '/api/runtime/state': () => ({ body: snapshotFixture() }),
      '/api/chat': () => ({ status: 500, body: { error: { code: 'internal_error', message: 'model exploded' } } }),
    });
    const client = makeClient();
    await client.connect();

    const result = await client.sendCommand('do something');
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/model exploded/);
    expect(client.store.getState().lastCommandError).toMatch(/model exploded/);
  });

  it('tracks in-flight state around a command', async () => {
    let resolveChat: ((value: unknown) => void) | null = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        if (String(input).startsWith('/api/chat')) {
          await new Promise((resolve) => {
            resolveChat = resolve;
          });
        }
        const body = String(input).startsWith('/api/chat')
          ? { conversationId: 'c', reply: 'ok', messages: [], toolCalls: [], pendingApprovals: [], events: [], model: 'm', provider: 'p', iterations: 1 }
          : snapshotFixture();
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    const client = makeClient();
    await client.connect();

    const pending = client.sendCommand('slow one');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(client.store.getState().commandInFlight).toBe(true);

    resolveChat!(undefined);
    await pending;
    expect(client.store.getState().commandInFlight).toBe(false);
  });
});

describe('approvals', () => {
  it('approve calls the runtime endpoint and re-reads state', async () => {
    let approved = false;
    const calls = stubFetch({
      '/api/runtime/state': () => ({ body: snapshotFixture() }),
      '/api/approvals/apr_1/approve': () => {
        approved = true;
        return { body: { ok: true, state: 'approved', message: 'Deleted x.txt (1 byte).' } };
      },
      '/api/approvals': () => ({ body: { approvals: approved ? [] : [approvalFixture] } }),
    });
    const client = makeClient();
    await client.connect();

    const outcome = await client.approve('apr_1');

    expect(outcome.ok).toBe(true);
    expect(calls.some((call) => call.url.includes('/approve') && call.method === 'POST')).toBe(true);
    // The card clears because the runtime says it is gone, not because a button was clicked.
    expect(client.store.getState().approvals).toHaveLength(0);
  });

  it('deny calls the runtime endpoint', async () => {
    const calls = stubFetch({
      '/api/runtime/state': () => ({ body: snapshotFixture() }),
      '/api/approvals/apr_1/deny': () => ({ body: { ok: true, state: 'denied', message: 'Denied.' } }),
      '/api/approvals': () => ({ body: { approvals: [] } }),
    });
    const client = makeClient();
    await client.connect();

    const outcome = await client.deny('apr_1');
    expect(outcome.ok).toBe(true);
    expect(calls.some((call) => call.url.includes('/deny') && call.method === 'POST')).toBe(true);
  });

  it('represents a rejected decision (409) as a failure and keeps the runtime’s view', async () => {
    stubFetch({
      '/api/runtime/state': () => ({ body: snapshotFixture() }),
      '/api/approvals/apr_1/approve': () => ({
        status: 409,
        body: { ok: false, state: 'unavailable', message: 'Approval apr_1 is expired and cannot be approved again.' },
      }),
      '/api/approvals': () => ({ body: { approvals: [] } }),
    });
    const client = makeClient();
    await client.connect();

    const outcome = await client.approve('apr_1');
    expect(outcome.ok).toBe(false);
    expect(outcome.message).toMatch(/expired/);
    expect(client.store.getState().lastCommandError).toMatch(/Approval failed/);
    expect(client.store.getState().approvals).toHaveLength(0);
  });

  it('represents a network failure during approval honestly', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        if (String(input).includes('/approve')) throw new Error('network down');
        return new Response(JSON.stringify(String(input).startsWith('/api/approvals') ? { approvals: [approvalFixture] } : snapshotFixture()), { status: 200 });
      }),
    );
    const client = makeClient();
    await client.connect();

    const outcome = await client.approve('apr_1');
    expect(outcome.ok).toBe(false);
    expect(client.store.getState().lastCommandError).toMatch(/network down/);
    // The request is still pending as far as the runtime is concerned.
    expect(client.store.getState().approvals).toHaveLength(1);
  });
});
