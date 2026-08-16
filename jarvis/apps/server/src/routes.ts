/**
 * The JARVIS HTTP API.
 *
 * Everything the UI can do goes through here. Two invariants hold across every
 * route: no provider secret is ever included in a response, and no route can
 * execute an approval-gated action without a recorded human decision.
 */
import type { MemoryType, TaskStatus } from '@jarvis/shared';
import { JarvisError, MEMORY_TYPES } from '@jarvis/shared';
import type { Jarvis } from '@jarvis/core';
import { VoiceUnavailableError } from '@jarvis/voice';
import { ProviderUnavailableError } from '@jarvis/providers';
import { Router, startSse, type RequestContext } from './http.ts';

function requiredString(value: unknown, field: string, max = 100_000): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new JarvisError(`"${field}" is required`, { status: 400, code: 'invalid_request' });
  }
  if (value.length > max) {
    throw new JarvisError(`"${field}" exceeds ${max} characters`, { status: 400, code: 'invalid_request' });
  }
  return value;
}

function intParam(query: URLSearchParams, name: string, fallback: number, max = 500): number {
  const raw = query.get(name);
  if (raw === null) return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed < 1) return fallback;
  return Math.min(parsed, max);
}

export function createRouter(jarvis: Jarvis): Router {
  const router = new Router();

  // ---------------------------------------------------------------- system
  router.get(
    '/api/health',
    (ctx) => {
      const database = jarvis.store.health();
      ctx.json(database.ok ? 200 : 503, {
        status: database.ok ? 'ok' : 'degraded',
        version: jarvis.status().version,
        database: { ok: database.ok, schemaVersion: database.schemaVersion },
        // Deliberately minimal: this endpoint is unauthenticated.
      });
    },
    { public: true },
  );

  router.get('/api/system/status', (ctx) => {
    ctx.json(200, {
      ...jarvis.status(),
      config: jarvis.publicConfig(),
      counts: {
        memories: jarvis.store.memories.count(ctx.userId),
        auditEvents: jarvis.store.audit.count(ctx.userId),
        pendingApprovals: jarvis.store.approvals.listPending(ctx.userId).length,
      },
    });
  });

  router.get('/api/system/config', (ctx) => {
    ctx.json(200, jarvis.publicConfig());
  });

  /**
   * One consistent snapshot of live runtime state, for control surfaces.
   *
   * Composition of systems that already exist — no new logic, same auth, same
   * redaction. A client rendering live state gets one atomic answer instead of
   * six round-trips that can disagree with each other.
   */
  router.get('/api/runtime/state', (ctx) => {
    ctx.json(200, jarvis.runtimeState(ctx.userId));
  });

  /** Measured process and host metrics. Unavailable fields are null, never invented. */
  router.get('/api/system/telemetry', (ctx) => {
    ctx.json(200, jarvis.telemetry.sample());
  });

  /** Live probe of the active model provider. Costs one tiny completion. */
  router.post('/api/system/provider-check', async (ctx) => {
    const result = await jarvis.provider.healthCheck();
    ctx.json(result.ok ? 200 : 503, {
      provider: jarvis.provider.id,
      model: jarvis.provider.model,
      ...result,
    });
  });

  // --------------------------------------------------------- conversations
  router.get('/api/conversations', (ctx) => {
    ctx.json(200, { conversations: jarvis.store.conversations.list(ctx.userId) });
  });

  router.post('/api/conversations', async (ctx) => {
    const body = await ctx.body<{ title?: string }>();
    jarvis.store.users.ensure(ctx.userId);
    const conversation = jarvis.store.conversations.create(ctx.userId, body.title || 'New conversation');
    ctx.json(201, { conversation });
  });

  router.get('/api/conversations/:id/messages', (ctx) => {
    const conversation = jarvis.store.conversations.get(ctx.params.id!);
    if (!conversation || conversation.userId !== ctx.userId) {
      throw new JarvisError('conversation not found', { status: 404, code: 'not_found' });
    }
    ctx.json(200, {
      conversation,
      messages: jarvis.store.messages.list(conversation.id, intParam(ctx.query, 'limit', 200)),
    });
  });

  router.delete('/api/conversations/:id', (ctx) => {
    const conversation = jarvis.store.conversations.get(ctx.params.id!);
    if (!conversation || conversation.userId !== ctx.userId) {
      throw new JarvisError('conversation not found', { status: 404, code: 'not_found' });
    }
    jarvis.store.conversations.delete(conversation.id);
    ctx.json(200, { deleted: conversation.id });
  });

  // ------------------------------------------------------------------ chat
  router.post('/api/chat', async (ctx) => {
    const body = await ctx.body<{ message?: string; conversationId?: string }>();
    const message = requiredString(body.message, 'message', 32_000);

    const turn = await jarvis.orchestrator.handleMessage({
      userId: ctx.userId,
      conversationId: body.conversationId ?? null,
      text: message,
    });

    ctx.json(200, turn);
  });

  // ---------------------------------------------------------------- memory
  router.get('/api/memories', (ctx) => {
    const query = ctx.query.get('query');
    const type = ctx.query.get('type') as MemoryType | null;
    const limit = intParam(ctx.query, 'limit', 50);

    if (query) {
      const options: { limit: number; type?: MemoryType } = { limit };
      if (type) options.type = type;
      ctx.json(200, { memories: jarvis.store.memories.search(ctx.userId, query, options) });
      return;
    }
    const options: { limit: number; type?: MemoryType } = { limit };
    if (type) options.type = type;
    ctx.json(200, { memories: jarvis.store.memories.list(ctx.userId, options) });
  });

  router.post('/api/memories', async (ctx) => {
    const body = await ctx.body<{
      type?: string;
      content?: string;
      importance?: number;
      confidence?: number;
      tags?: string[];
    }>();
    const content = requiredString(body.content, 'content', 4000);
    const type = requiredString(body.type, 'type', 40) as MemoryType;
    if (!(MEMORY_TYPES as readonly string[]).includes(type)) {
      throw new JarvisError(`"type" must be one of: ${MEMORY_TYPES.join(', ')}`, {
        status: 400,
        code: 'invalid_request',
      });
    }

    jarvis.store.users.ensure(ctx.userId);
    const memory = jarvis.store.memories.write({
      userId: ctx.userId,
      type,
      content,
      source: 'user',
      importance: typeof body.importance === 'number' ? body.importance : 0.6,
      confidence: typeof body.confidence === 'number' ? body.confidence : 1,
      tags: Array.isArray(body.tags) ? body.tags.map(String) : [],
    });
    jarvis.events.emit({
      type: 'MEMORY_WRITE',
      userId: ctx.userId,
      agent: 'user',
      summary: `User saved a ${memory.type} memory.`,
      data: { id: memory.id },
    });
    ctx.json(201, { memory });
  });

  router.delete('/api/memories/:id', (ctx) => {
    const memory = jarvis.store.memories.get(ctx.params.id!);
    if (!memory || memory.userId !== ctx.userId) {
      throw new JarvisError('memory not found', { status: 404, code: 'not_found' });
    }
    jarvis.store.memories.delete(memory.id);
    ctx.json(200, { deleted: memory.id });
  });

  // ----------------------------------------------------------------- tasks
  router.get('/api/tasks', (ctx) => {
    const status = ctx.query.get('status') as TaskStatus | null;
    const options: { limit: number; status?: TaskStatus } = { limit: intParam(ctx.query, 'limit', 100) };
    if (status) options.status = status;
    ctx.json(200, { tasks: jarvis.store.tasks.list(ctx.userId, options) });
  });

  router.post('/api/tasks', async (ctx) => {
    const body = await ctx.body<{ title?: string; detail?: string }>();
    jarvis.store.users.ensure(ctx.userId);
    const task = jarvis.store.tasks.create({
      userId: ctx.userId,
      title: requiredString(body.title, 'title', 200),
      detail: typeof body.detail === 'string' ? body.detail : '',
    });
    ctx.json(201, { task });
  });

  // ----------------------------------------------------------------- tools
  router.get('/api/tools', (ctx) => {
    const infos = jarvis.registry.infos((tool) => {
      // A tool is "available" when its backing capability is configured.
      if (tool.name === 'web_search' && !jarvis.search.isAvailable()) {
        return { available: false, reason: jarvis.search.status().reason ?? 'search not configured' };
      }
      return { available: true };
    });
    ctx.json(200, {
      tools: infos,
      approvalRequiredLevels: [...jarvis.policy.requiredLevels],
    });
  });

  // ---------------------------------------------------------------- agents
  router.get('/api/agents', (ctx) => {
    const records = jarvis.store.agents.list();
    const agents = Object.values(jarvis.agents).map((definition) => {
      const record = records.find((row) => row.name === definition.name);
      return {
        name: definition.name,
        title: definition.title,
        purpose: definition.purpose,
        maxRisk: definition.maxRisk,
        readOnly: definition.readOnly,
        maxIterations: definition.maxIterations,
        tools: jarvis.registry.listForAgent(definition).map((tool) => tool.name),
        runCount: record?.runCount ?? 0,
        lastRunAt: record?.lastRunAt ?? null,
      };
    });
    ctx.json(200, { agents, charterErrors: jarvis.charterErrors });
  });

  /** Run an agent directly, bypassing the orchestrator's routing decision. */
  router.post('/api/agents/:name/run', async (ctx) => {
    const name = ctx.params.name!;
    const definition = jarvis.agents[name];
    if (!definition) throw new JarvisError(`unknown agent "${name}"`, { status: 404, code: 'not_found' });

    const body = await ctx.body<{ task?: string; conversationId?: string; briefing?: string }>();
    const task = requiredString(body.task, 'task', 8000);
    jarvis.store.users.ensure(ctx.userId);

    const result = await jarvis.runner.run(definition, task, {
      userId: ctx.userId,
      conversationId: body.conversationId ?? null,
      memories: jarvis.orchestrator.retrieveContext(ctx.userId, task, 4),
      ...(body.briefing ? { briefing: body.briefing } : {}),
    });
    ctx.json(200, { result });
  });

  // ------------------------------------------------------------- approvals
  router.get('/api/approvals', (ctx) => {
    const all = ctx.query.get('state') === 'all';
    ctx.json(200, {
      approvals: all
        ? jarvis.store.approvals.list(ctx.userId, intParam(ctx.query, 'limit', 100))
        : jarvis.store.approvals.listPending(ctx.userId),
    });
  });

  router.post('/api/approvals/:id/approve', async (ctx) => {
    const body = await ctx.body<{ note?: string }>();
    const outcome = await jarvis.approve(ctx.params.id!, ctx.userId, body.note);
    ctx.json(outcome.state === 'unavailable' ? 409 : 200, outcome);
  });

  router.post('/api/approvals/:id/deny', async (ctx) => {
    const body = await ctx.body<{ note?: string }>();
    const outcome = await jarvis.deny(ctx.params.id!, ctx.userId, body.note);
    ctx.json(outcome.state === 'unavailable' ? 409 : 200, outcome);
  });

  // ------------------------------------------------------------ audit/logs
  router.get('/api/audit', (ctx) => {
    const tool = ctx.query.get('tool');
    const options: { limit: number; tool?: string } = { limit: intParam(ctx.query, 'limit', 100) };
    if (tool) options.tool = tool;
    ctx.json(200, { events: jarvis.store.audit.list(ctx.userId, options) });
  });

  router.get('/api/events', (ctx) => {
    const conversationId = ctx.query.get('conversationId');
    const types = ctx.query.get('types');
    const options: { limit: number; conversationId?: string; types?: string[] } = {
      limit: intParam(ctx.query, 'limit', 100),
    };
    if (conversationId) options.conversationId = conversationId;
    if (types) options.types = types.split(',').filter(Boolean);
    ctx.json(200, { events: jarvis.store.events.list(ctx.userId, options) });
  });

  /**
   * The live stream. One connection carries three frame types:
   *
   *   `state`     — a full runtime snapshot, sent on connect and on reconnect,
   *                 so a client resynchronises without a separate fetch and
   *                 without inferring state it missed while disconnected.
   *   `jarvis`    — every event on the bus, as it happens.
   *   `telemetry` — a periodic measured sample. Not persisted to the event log:
   *                 it is a reading, not something that happened.
   */
  router.get('/api/events/stream', (ctx) => {
    const send = ctx.sse();

    send('state', jarvis.runtimeState(ctx.userId));
    for (const event of jarvis.events.recent(25)) send('jarvis', event);

    const unsubscribe = jarvis.events.subscribe((event) => send('jarvis', event));

    const telemetryTick = setInterval(() => {
      if (ctx.res.writableEnded) return;
      // Sweeping here means an expiry is announced on the same stream that
      // delivered the request, rather than waiting for the next page load.
      jarvis.sweepExpiredApprovals();
      send('telemetry', {
        telemetry: jarvis.telemetry.sample(),
        activity: jarvis.monitor.snapshot(),
      });
    }, 5_000);

    const keepAlive = setInterval(() => {
      if (!ctx.res.writableEnded) ctx.res.write(': keep-alive\n\n');
    }, 25_000);

    const close = () => {
      clearInterval(keepAlive);
      clearInterval(telemetryTick);
      unsubscribe();
      if (!ctx.res.writableEnded) ctx.res.end();
    };
    ctx.req.on('close', close);
    ctx.req.on('error', close);
  });

  // ----------------------------------------------------------------- voice
  router.get('/api/voice/status', (ctx) => {
    ctx.json(200, {
      stt: { ...jarvis.stt.status(), mode: jarvis.stt.mode },
      tts: { ...jarvis.tts.status(), mode: jarvis.tts.mode },
    });
  });

  router.post('/api/voice/transcribe', async (ctx) => {
    const body = await ctx.body<{ audioB64?: string; mimeType?: string; languageCode?: string }>();
    if (jarvis.stt.mode === 'browser') {
      throw new JarvisError(jarvis.stt.status().reason ?? 'speech-to-text runs in the browser', {
        status: 501,
        code: 'client_side_capability',
      });
    }
    const audioB64 = requiredString(body.audioB64, 'audioB64', 20_000_000);
    try {
      const result = await jarvis.stt.transcribe({
        audioB64,
        mimeType: body.mimeType ?? 'audio/wav',
        ...(body.languageCode ? { languageCode: body.languageCode } : {}),
      });
      ctx.json(200, result);
    } catch (error) {
      if (error instanceof VoiceUnavailableError) {
        throw new JarvisError(error.message, { status: 503, code: 'provider_unavailable' });
      }
      throw error;
    }
  });

  router.post('/api/voice/speak', async (ctx) => {
    const body = await ctx.body<{ text?: string; voice?: string }>();
    if (jarvis.tts.mode === 'browser') {
      throw new JarvisError(jarvis.tts.status().reason ?? 'text-to-speech runs in the browser', {
        status: 501,
        code: 'client_side_capability',
      });
    }
    const text = requiredString(body.text, 'text', 8000);
    try {
      const result = await jarvis.tts.synthesize({ text, ...(body.voice ? { voice: body.voice } : {}) });
      ctx.json(200, result);
    } catch (error) {
      if (error instanceof VoiceUnavailableError) {
        throw new JarvisError(error.message, { status: 503, code: 'provider_unavailable' });
      }
      throw error;
    }
  });

  // ----------------------------------------------------------------- media
  const mediaError = (error: unknown): never => {
    if (error instanceof ProviderUnavailableError) {
      throw new JarvisError(error.message, { status: 503, code: 'provider_unavailable' });
    }
    throw error;
  };

  router.get('/api/media/status', (ctx) => {
    ctx.json(200, {
      image: jarvis.image.status(),
      imageEdit: jarvis.imageEdit.status(),
      video: jarvis.video.status(),
      vision: jarvis.vision.status(),
    });
  });

  router.post('/api/media/image', async (ctx) => {
    const body = await ctx.body<{ prompt?: string; width?: number; height?: number; seed?: number }>();
    const prompt = requiredString(body.prompt, 'prompt', 4000);
    try {
      const image = await jarvis.image.generate({
        prompt,
        ...(body.width ? { width: body.width } : {}),
        ...(body.height ? { height: body.height } : {}),
        ...(body.seed !== undefined ? { seed: body.seed } : {}),
      });
      ctx.json(200, image);
    } catch (error) {
      mediaError(error);
    }
  });

  router.post('/api/media/image/edit', async (ctx) => {
    const body = await ctx.body<{ prompt?: string; imageB64?: string; strength?: number }>();
    const prompt = requiredString(body.prompt, 'prompt', 4000);
    const imageB64 = requiredString(body.imageB64, 'imageB64', 20_000_000);
    try {
      const image = await jarvis.imageEdit.edit({
        prompt,
        imageB64,
        ...(body.strength !== undefined ? { strength: body.strength } : {}),
      });
      ctx.json(200, image);
    } catch (error) {
      mediaError(error);
    }
  });

  router.post('/api/media/video', async (ctx) => {
    const body = await ctx.body<{ prompt?: string; durationSeconds?: number }>();
    const prompt = requiredString(body.prompt, 'prompt', 4000);
    try {
      const video = await jarvis.video.generate({
        prompt,
        ...(body.durationSeconds ? { durationSeconds: body.durationSeconds } : {}),
      });
      ctx.json(200, video);
    } catch (error) {
      mediaError(error);
    }
  });

  router.post('/api/media/vision', async (ctx) => {
    const body = await ctx.body<{ imageB64?: string; prompt?: string }>();
    const imageB64 = requiredString(body.imageB64, 'imageB64', 20_000_000);
    try {
      const described = await jarvis.vision.describe({
        imageB64,
        ...(body.prompt ? { prompt: body.prompt } : {}),
      });
      ctx.json(200, described);
    } catch (error) {
      mediaError(error);
    }
  });

  return router;
}

export type { RequestContext };
