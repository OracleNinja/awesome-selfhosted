/**
 * HTTP API tests.
 *
 * These start the real server on an ephemeral port and talk to it over real
 * HTTP — no handler is called directly — so routing, auth, JSON handling and
 * status codes are all genuinely exercised.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { Server } from 'node:http';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startJarvisServer } from '@jarvis/server/src/server.ts';
import { createTestJarvis, type TestHarness } from './helpers.ts';

interface ApiClient {
  request: (method: string, path: string, body?: unknown, token?: string) => Promise<{
    status: number;
    body: any;
  }>;
}

function client(baseUrl: string, defaultToken?: string): ApiClient {
  return {
    async request(method, path, body, token = defaultToken) {
      const response = await fetch(`${baseUrl}${path}`, {
        method,
        headers: {
          'content-type': 'application/json',
          ...(token ? { authorization: `Bearer ${token}` } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const text = await response.text();
      return { status: response.status, body: text ? JSON.parse(text) : null };
    },
  };
}

describe('API (local mode, no token)', () => {
  let harness: TestHarness;
  let server: Server;
  let api: ApiClient;

  beforeAll(async () => {
    // Tests in this suite share one server; individual tests re-script the
    // provider when they need a specific model behaviour.
    harness = createTestJarvis({ turns: [{ content: 'Acknowledged.' }] });
    const started = await startJarvisServer({ jarvis: harness.jarvis, port: 0, host: '127.0.0.1' });
    server = started.server;
    api = client(`http://127.0.0.1:${started.port}`);
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    harness.cleanup();
  });

  it('serves an unauthenticated health check', async () => {
    const { status, body } = await api.request('GET', '/api/health');
    expect(status).toBe(200);
    expect(body.status).toBe('ok');
    expect(body.database.ok).toBe(true);
  });

  it('reports system status including provider availability', async () => {
    const { status, body } = await api.request('GET', '/api/system/status');
    expect(status).toBe(200);
    expect(body.version).toBe('0.1.0');
    expect(body.agents.sort()).toEqual(['advisor', 'developer', 'operator', 'scout']);
    expect(body.tools.total).toBeGreaterThanOrEqual(12);
    expect(body.tools.requiringApproval).toBeGreaterThan(0);
    expect(Array.isArray(body.providers)).toBe(true);
    for (const provider of body.providers) {
      if (!provider.available) expect(provider.reason).toBeTruthy();
    }
  });

  it('never sends an API key to the browser', async () => {
    harness.jarvis.config.nvidia.apiKey = 'nvapi-super-secret-value-9999';
    harness.jarvis.config.anthropic.apiKey = 'sk-ant-super-secret-8888';

    for (const path of ['/api/system/status', '/api/system/config', '/api/tools', '/api/agents']) {
      const { body } = await api.request('GET', path);
      const serialised = JSON.stringify(body);
      expect(serialised, path).not.toContain('nvapi-super-secret-value-9999');
      expect(serialised, path).not.toContain('sk-ant-super-secret-8888');
      expect(serialised, path).not.toMatch(/apiKey|api_key/);
    }
  });

  it('creates a conversation, sends a message and persists it', async () => {
    const created = await api.request('POST', '/api/conversations', { title: 'Ops' });
    expect(created.status).toBe(201);
    const conversationId = created.body.conversation.id;

    const chat = await api.request('POST', '/api/chat', {
      conversationId,
      message: 'Report status.',
    });
    expect(chat.status).toBe(200);
    expect(chat.body.reply).toBe('Acknowledged.');
    expect(chat.body.provider).toBe('scripted');

    const messages = await api.request('GET', `/api/conversations/${conversationId}/messages`);
    expect(messages.status).toBe(200);
    expect(messages.body.messages).toHaveLength(2);
    expect(messages.body.messages[0].content).toBe('Report status.');

    const list = await api.request('GET', '/api/conversations');
    expect(list.body.conversations.some((c: { id: string }) => c.id === conversationId)).toBe(true);
  });

  it('rejects a chat request with no message', async () => {
    const { status, body } = await api.request('POST', '/api/chat', {});
    expect(status).toBe(400);
    expect(body.error.code).toBe('invalid_request');
  });

  it('rejects malformed JSON', async () => {
    const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/api/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{ not json',
    });
    expect(response.status).toBe(400);
  });

  it('returns 404 for unknown routes and unknown resources', async () => {
    expect((await api.request('GET', '/api/nope')).status).toBe(404);
    expect((await api.request('GET', '/api/conversations/conv_missing/messages')).status).toBe(404);
    expect((await api.request('POST', '/api/agents/butler/run', { task: 'x' })).status).toBe(404);
  });

  it('creates and searches memories through the API', async () => {
    const created = await api.request('POST', '/api/memories', {
      type: 'preference',
      content: 'Prefers dark mode interfaces at night.',
      importance: 0.7,
    });
    expect(created.status).toBe(201);

    const searched = await api.request('GET', '/api/memories?query=dark%20mode%20interface');
    expect(searched.status).toBe(200);
    expect(searched.body.memories[0].content).toMatch(/dark mode/);

    const deleted = await api.request('DELETE', `/api/memories/${created.body.memory.id}`);
    expect(deleted.status).toBe(200);
  });

  it('rejects an invalid memory type', async () => {
    const { status, body } = await api.request('POST', '/api/memories', {
      type: 'vibes',
      content: 'nope',
    });
    expect(status).toBe(400);
    expect(body.error.message).toMatch(/must be one of/);
  });

  it('lists tools with risk levels and approval flags', async () => {
    const { body } = await api.request('GET', '/api/tools');
    const byName = Object.fromEntries(body.tools.map((tool: { name: string }) => [tool.name, tool]));
    expect(byName.current_time.risk).toBe('READ');
    expect(byName.current_time.requiresApproval).toBe(false);
    expect(byName.file_delete.risk).toBe('DESTRUCTIVE');
    expect(byName.file_delete.requiresApproval).toBe(true);
    expect(byName.web_search.available).toBe(false);
    expect(byName.web_search.unavailableReason).toBeTruthy();
    expect(body.approvalRequiredLevels).toEqual(['EXTERNAL_ACTION', 'DESTRUCTIVE']);
  });

  it('lists agents with their permitted tools', async () => {
    const { body } = await api.request('GET', '/api/agents');
    const scout = body.agents.find((agent: { name: string }) => agent.name === 'scout');
    expect(scout.readOnly).toBe(true);
    expect(scout.tools).toContain('memory_search');
    expect(scout.tools).not.toContain('file_delete');
  });

  it('drives the full approval workflow over HTTP', async () => {
    writeFileSync(join(harness.workspace, 'target.txt'), 'delete me');
    harness.provider.script([
      { toolCalls: [{ name: 'file_delete', arguments: { path: 'target.txt' } }] },
      { content: 'I need approval to delete target.txt.' },
      { content: 'Deleted, as approved.' },
    ]);

    const chat = await api.request('POST', '/api/chat', { message: 'delete target.txt' });
    expect(chat.status).toBe(200);
    expect(chat.body.pendingApprovals).toHaveLength(1);

    const pending = await api.request('GET', '/api/approvals');
    expect(pending.body.approvals).toHaveLength(1);
    const approval = pending.body.approvals[0];
    expect(approval.risk).toBe('DESTRUCTIVE');

    const approved = await api.request('POST', `/api/approvals/${approval.id}/approve`, {});
    expect(approved.status).toBe(200);
    expect(approved.body.ok).toBe(true);

    // Deciding twice is a conflict, not a second execution.
    const again = await api.request('POST', `/api/approvals/${approval.id}/approve`, {});
    expect(again.status).toBe(409);

    const audit = await api.request('GET', '/api/audit');
    expect(audit.body.events.some((entry: { tool: string }) => entry.tool === 'file_delete')).toBe(true);
  });

  it('exposes the activity event log', async () => {
    const { body } = await api.request('GET', '/api/events?limit=50');
    const types = body.events.map((event: { type: string }) => event.type);
    expect(types).toContain('USER_MESSAGE');
    expect(types).toContain('MODEL_RESPONSE');
  });

  it('reports voice and media capability status honestly', async () => {
    const voice = await api.request('GET', '/api/voice/status');
    expect(voice.body.stt.mode).toBe('browser');
    expect(voice.body.tts.mode).toBe('browser');

    // Browser-mode voice is a client-side capability: the server says so.
    const transcribe = await api.request('POST', '/api/voice/transcribe', { audioB64: 'AAAA' });
    expect(transcribe.status).toBe(501);
    expect(transcribe.body.error.code).toBe('client_side_capability');

    const media = await api.request('GET', '/api/media/status');
    expect(media.body.image.available).toBe(false);
    expect(media.body.image.reason).toMatch(/IMAGE_PROVIDER/);
    expect(media.body.video.available).toBe(false);
    expect(media.body.vision.available).toBe(false);
  });

  it('returns 503, not a fabricated image, when generation is unconfigured', async () => {
    const { status, body } = await api.request('POST', '/api/media/image', { prompt: 'a red cube' });
    expect(status).toBe(503);
    expect(body.error.code).toBe('provider_unavailable');
  });

  it('streams live events over SSE', async () => {
    const port = (server.address() as { port: number }).port;
    const controller = new AbortController();
    const response = await fetch(`http://127.0.0.1:${port}/api/events/stream`, {
      signal: controller.signal,
    });
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toMatch(/text\/event-stream/);

    const reader = response.body!.getReader();
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toContain('connected');

    controller.abort();
    await reader.cancel().catch(() => {});
  });
});

describe('API (token mode)', () => {
  let harness: TestHarness;
  let server: Server;
  let api: ApiClient;
  const token = 'test-token-0123456789abcdef';

  beforeAll(async () => {
    harness = createTestJarvis({ config: { apiToken: token }, turns: [{ content: 'ok' }] });
    const started = await startJarvisServer({ jarvis: harness.jarvis, port: 0, host: '127.0.0.1' });
    server = started.server;
    api = client(`http://127.0.0.1:${started.port}`, token);
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    harness.cleanup();
  });

  it('accepts a correct bearer token', async () => {
    const { status } = await api.request('GET', '/api/system/status');
    expect(status).toBe(200);
  });

  it('rejects a missing token', async () => {
    const { status, body } = await api.request('GET', '/api/system/status', undefined, '');
    expect(status).toBe(401);
    expect(body.error.code).toBe('unauthorized');
  });

  it('rejects a wrong token', async () => {
    const { status } = await api.request('GET', '/api/system/status', undefined, 'wrong-token-value');
    expect(status).toBe(401);
  });

  it('rejects a token of the correct length but wrong value', async () => {
    const sameLength = 'x'.repeat(token.length);
    const { status } = await api.request('GET', '/api/system/status', undefined, sameLength);
    expect(status).toBe(401);
  });

  it('still serves health unauthenticated', async () => {
    const { status } = await api.request('GET', '/api/health', undefined, '');
    expect(status).toBe(200);
  });

  it('protects mutating routes', async () => {
    const { status } = await api.request('POST', '/api/chat', { message: 'hi' }, '');
    expect(status).toBe(401);
  });

  it('accepts the session token as a query parameter on the SSE route only', async () => {
    const port = (server.address() as { port: number }).port;

    // EventSource cannot set headers, so the stream route accepts ?token=.
    const stream = await fetch(`http://127.0.0.1:${port}/api/events/stream?token=${token}`);
    expect(stream.status).toBe(200);
    await stream.body?.cancel();

    // A wrong token on the same route is still rejected.
    const badStream = await fetch(`http://127.0.0.1:${port}/api/events/stream?token=nope`);
    expect(badStream.status).toBe(401);

    // And no other route honours the query parameter.
    const other = await fetch(`http://127.0.0.1:${port}/api/system/status?token=${token}`);
    expect(other.status).toBe(401);
  });
});

describe('static hosting', () => {
  let harness: TestHarness;
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    harness = createTestJarvis({ turns: [{ content: 'ok' }] });
    // Serve the repo's docs directory as a stand-in web root: the point is the
    // path handling, not the specific files.
    const webDir = join(process.cwd(), 'docs');
    const started = await startJarvisServer({
      jarvis: harness.jarvis,
      port: 0,
      host: '127.0.0.1',
      webDir,
    });
    server = started.server;
    baseUrl = `http://127.0.0.1:${started.port}`;
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    harness.cleanup();
  });

  it('serves a file from the web root', async () => {
    const response = await fetch(`${baseUrl}/SECURITY.md`);
    expect(response.status).toBe(200);
    expect(await response.text()).toContain('# Security');
  });

  it('rejects path traversal out of the web root', async () => {
    const response = await fetch(`${baseUrl}/..%2f..%2fpackage.json`);
    expect(response.status).not.toBe(200);
  });

  it('does not let static hosting shadow the API', async () => {
    const response = await fetch(`${baseUrl}/api/health`);
    expect(response.headers.get('content-type')).toMatch(/application\/json/);
  });
});
