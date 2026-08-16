/**
 * Model provider tests.
 *
 * The NVIDIA provider is exercised against a real HTTP server speaking the
 * OpenAI-compatible wire format on localhost, so the request body, the auth
 * header, tool serialisation and tool-call parsing are all genuinely tested —
 * not stubbed at the class boundary.
 */
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import {
  AnthropicProvider,
  NvidiaProvider,
  ProviderError,
  ProviderUnavailableError,
  createModelProvider,
  modelProviderStatuses,
  toAnthropicMessages,
  parseToolCalls,
  toOpenAIMessages,
} from '@jarvis/providers';
import { testConfig } from './helpers.ts';

interface CapturedRequest {
  path: string;
  headers: Record<string, string | string[] | undefined>;
  body: Record<string, unknown>;
}

/** A stand-in NVIDIA NIM endpoint. */
function startFakeInference(handler: (req: CapturedRequest) => { status: number; body: unknown }): Promise<{
  server: Server;
  baseUrl: string;
  captured: CapturedRequest[];
}> {
  const captured: CapturedRequest[] = [];
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk) => chunks.push(chunk as Buffer));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      const request: CapturedRequest = {
        path: req.url ?? '',
        headers: req.headers,
        body: raw ? (JSON.parse(raw) as Record<string, unknown>) : {},
      };
      captured.push(request);
      const { status, body } = handler(request);
      const payload = JSON.stringify(body);
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(payload);
    });
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo;
      resolve({ server, baseUrl: `http://127.0.0.1:${port}/v1`, captured });
    });
  });
}

describe('provider routing', () => {
  it('selects the provider named by MODEL_PROVIDER', () => {
    expect(createModelProvider(testConfig({ modelProvider: 'nvidia' })).id).toBe('nvidia');
    expect(createModelProvider(testConfig({ modelProvider: 'anthropic' })).id).toBe('anthropic');
    expect(createModelProvider(testConfig({ modelProvider: 'openai' })).id).toBe('openai');
    expect(createModelProvider(testConfig({ modelProvider: 'local' })).id).toBe('local');
  });

  it('can construct a provider other than the configured one', () => {
    const config = testConfig({ modelProvider: 'nvidia' });
    expect(createModelProvider(config, 'anthropic').id).toBe('anthropic');
  });

  it('reports every provider’s availability with a reason', () => {
    const statuses = modelProviderStatuses(testConfig());
    expect(statuses.map((s) => s.id).sort()).toEqual(['anthropic', 'local', 'nvidia', 'openai']);
    for (const status of statuses) {
      if (!status.available) expect(status.reason).toBeTruthy();
    }
  });

  it('never exposes an API key in a provider status', () => {
    const config = testConfig();
    config.nvidia.apiKey = 'nvapi-secret-value-abcdefghijklmnop';
    const serialised = JSON.stringify(modelProviderStatuses(config));
    expect(serialised).not.toContain('nvapi-secret-value');
  });
});

describe('NVIDIA provider', () => {
  let fake: Awaited<ReturnType<typeof startFakeInference>> | null = null;

  afterEach(() => {
    fake?.server.close();
    fake = null;
  });

  it('is unavailable without an API key, and says why', async () => {
    const provider = new NvidiaProvider({ apiKey: '', baseUrl: 'https://integrate.api.nvidia.com/v1', model: 'm' });
    expect(provider.isAvailable()).toBe(false);
    expect(provider.status().reason).toMatch(/API key/i);
    await expect(provider.chat({ messages: [{ role: 'user', content: 'hi' }] })).rejects.toBeInstanceOf(
      ProviderUnavailableError,
    );
  });

  it('is unavailable without a model', () => {
    const provider = new NvidiaProvider({ apiKey: 'k', baseUrl: 'https://x/v1', model: '' });
    expect(provider.isAvailable()).toBe(false);
    expect(provider.status().reason).toMatch(/model/i);
  });

  it('sends an OpenAI-compatible request with bearer auth and returns the completion', async () => {
    fake = await startFakeInference(() => ({
      status: 200,
      body: {
        model: 'meta/llama-3.3-70b-instruct',
        choices: [{ message: { content: 'All systems nominal.' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 11, completion_tokens: 5 },
      },
    }));

    const provider = new NvidiaProvider({
      apiKey: 'nvapi-test-key',
      baseUrl: fake.baseUrl,
      model: 'meta/llama-3.3-70b-instruct',
    });
    expect(provider.isAvailable()).toBe(true);

    const response = await provider.chat({
      messages: [
        { role: 'system', content: 'You are JARVIS.' },
        { role: 'user', content: 'status?' },
      ],
      temperature: 0.2,
    });

    expect(response.content).toBe('All systems nominal.');
    expect(response.finishReason).toBe('stop');
    expect(response.providerId).toBe('nvidia');
    expect(response.usage).toEqual({ promptTokens: 11, completionTokens: 5 });

    const request = fake.captured[0]!;
    expect(request.path).toBe('/v1/chat/completions');
    expect(request.headers.authorization).toBe('Bearer nvapi-test-key');
    expect(request.body.model).toBe('meta/llama-3.3-70b-instruct');
    expect(request.body.messages).toEqual([
      { role: 'system', content: 'You are JARVIS.' },
      { role: 'user', content: 'status?' },
    ]);
  });

  it('serialises tools and parses tool calls back out', async () => {
    fake = await startFakeInference(() => ({
      status: 200,
      body: {
        choices: [
          {
            message: {
              content: null,
              tool_calls: [
                {
                  id: 'call_abc',
                  type: 'function',
                  function: { name: 'current_time', arguments: '{"timezone":"UTC"}' },
                },
              ],
            },
            finish_reason: 'tool_calls',
          },
        ],
      },
    }));

    const provider = new NvidiaProvider({ apiKey: 'k', baseUrl: fake.baseUrl, model: 'm' });
    const response = await provider.chat({
      messages: [{ role: 'user', content: 'what time is it' }],
      tools: [
        {
          name: 'current_time',
          description: 'Get the time',
          parameters: { type: 'object', properties: { timezone: { type: 'string' } }, required: [] },
        },
      ],
    });

    expect(response.finishReason).toBe('tool_calls');
    expect(response.toolCalls).toEqual([
      { id: 'call_abc', name: 'current_time', arguments: { timezone: 'UTC' } },
    ]);

    const sent = fake.captured[0]!.body as { tools?: unknown[]; tool_choice?: string };
    expect(sent.tool_choice).toBe('auto');
    expect(sent.tools).toEqual([
      {
        type: 'function',
        function: {
          name: 'current_time',
          description: 'Get the time',
          parameters: { type: 'object', properties: { timezone: { type: 'string' } }, required: [] },
        },
      },
    ]);
  });

  it('surfaces HTTP errors as ProviderError with the endpoint message', async () => {
    fake = await startFakeInference(() => ({
      status: 401,
      body: { error: { message: 'invalid api key' } },
    }));

    const provider = new NvidiaProvider({ apiKey: 'bad', baseUrl: fake.baseUrl, model: 'm' });
    await expect(provider.chat({ messages: [{ role: 'user', content: 'hi' }] })).rejects.toThrow(
      /HTTP 401.*invalid api key/,
    );
    await expect(
      provider.chat({ messages: [{ role: 'user', content: 'hi' }] }),
    ).rejects.toBeInstanceOf(ProviderError);
  });

  it('healthCheck reports failure rather than throwing', async () => {
    fake = await startFakeInference(() => ({ status: 500, body: { error: { message: 'boom' } } }));
    const provider = new NvidiaProvider({ apiKey: 'k', baseUrl: fake.baseUrl, model: 'm' });
    const result = await provider.healthCheck();
    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/HTTP 500/);
  });

  it('treats a localhost endpoint as not requiring a key (self-hosted NIM)', () => {
    const provider = new NvidiaProvider({ apiKey: '', baseUrl: 'http://localhost:8000/v1', model: 'm' });
    expect(provider.isAvailable()).toBe(true);
  });
});

describe('OpenAI wire-format conversion', () => {
  it('maps assistant tool calls and tool results', () => {
    const messages = toOpenAIMessages([
      { role: 'user', content: 'time?' },
      {
        role: 'assistant',
        content: '',
        toolCalls: [{ id: 'c1', name: 'current_time', arguments: { timezone: 'UTC' } }],
      },
      { role: 'tool', toolCallId: 'c1', content: '{"ok":true}' },
    ]);

    expect(messages[1]).toEqual({
      role: 'assistant',
      content: null,
      tool_calls: [
        { id: 'c1', type: 'function', function: { name: 'current_time', arguments: '{"timezone":"UTC"}' } },
      ],
    });
    expect(messages[2]).toEqual({ role: 'tool', tool_call_id: 'c1', content: '{"ok":true}' });
  });

  it('does not crash on malformed tool-call JSON', () => {
    const calls = parseToolCalls(
      [{ id: 'x', function: { name: 'file_read', arguments: '{not json' } }],
      'nvidia',
    );
    expect(calls).toHaveLength(1);
    expect(calls[0]!.arguments).toHaveProperty('__unparsed_arguments');
  });
});

describe('Anthropic provider', () => {
  it('is unavailable without a key', () => {
    const provider = new AnthropicProvider({ apiKey: '', baseUrl: '', model: 'claude-sonnet-4-5' });
    expect(provider.isAvailable()).toBe(false);
    expect(provider.status().reason).toMatch(/ANTHROPIC_API_KEY/);
  });

  it('hoists system messages and converts tool blocks', () => {
    const { system, messages } = toAnthropicMessages([
      { role: 'system', content: 'You are JARVIS.' },
      { role: 'user', content: 'time?' },
      {
        role: 'assistant',
        content: 'checking',
        toolCalls: [{ id: 'c1', name: 'current_time', arguments: {} }],
      },
      { role: 'tool', toolCallId: 'c1', content: 'noon' },
    ]);

    expect(system).toBe('You are JARVIS.');
    expect(messages[0]).toEqual({ role: 'user', content: 'time?' });
    expect(messages[1]).toEqual({
      role: 'assistant',
      content: [
        { type: 'text', text: 'checking' },
        { type: 'tool_use', id: 'c1', name: 'current_time', input: {} },
      ],
    });
    expect(messages[2]).toEqual({
      role: 'user',
      content: [{ type: 'tool_result', tool_use_id: 'c1', content: 'noon' }],
    });
  });
});

describe('local provider placeholder', () => {
  it('is unavailable until LOCAL_BASE_URL is set, then becomes usable', () => {
    const unset = createModelProvider(testConfig({ modelProvider: 'local' }));
    expect(unset.isAvailable()).toBe(false);
    expect(unset.status().reason).toMatch(/base URL/i);

    const config = testConfig({ modelProvider: 'local' });
    config.local = { apiKey: '', baseUrl: 'http://127.0.0.1:11434/v1', model: 'llama3' };
    expect(createModelProvider(config).isAvailable()).toBe(true);
  });
});
