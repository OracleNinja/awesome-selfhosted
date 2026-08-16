import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  loadConfig,
  parseEnvFile,
  publicConfig,
  redact,
  repoRoot,
  resolveDatabasePath,
} from '@jarvis/shared';

describe('.env parsing', () => {
  it('parses assignments, ignores comments and strips quotes', () => {
    const parsed = parseEnvFile(
      ['# a comment', 'NVIDIA_API_KEY=nvapi-123', 'QUOTED="hello world"', '', 'EMPTY=', 'bad line'].join('\n'),
    );
    expect(parsed.NVIDIA_API_KEY).toBe('nvapi-123');
    expect(parsed.QUOTED).toBe('hello world');
    expect(parsed.EMPTY).toBe('');
    expect(parsed['bad line']).toBeUndefined();
  });
});

describe('configuration', () => {
  it('reads provider settings from the environment', () => {
    const config = loadConfig({
      MODEL_PROVIDER: 'nvidia',
      NVIDIA_API_KEY: 'nvapi-test',
      NVIDIA_BASE_URL: 'https://example.invalid/v1',
      NVIDIA_MODEL: 'meta/llama-3.3-70b-instruct',
      PORT: '9000',
    });
    expect(config.modelProvider).toBe('nvidia');
    expect(config.nvidia.apiKey).toBe('nvapi-test');
    expect(config.nvidia.model).toBe('meta/llama-3.3-70b-instruct');
    expect(config.port).toBe(9000);
  });

  it('falls back to safe defaults for unset or invalid values', () => {
    const config = loadConfig({ MODEL_PROVIDER: 'not-a-provider', PORT: 'abc' });
    expect(config.modelProvider).toBe('nvidia');
    expect(config.port).toBe(8787);
    expect(config.search.provider).toBe('none');
    expect(config.image.provider).toBe('none');
    expect(config.stt.provider).toBe('browser');
  });

  it('parses the approval policy from the environment', () => {
    expect(loadConfig({ APPROVAL_REQUIRED_LEVELS: 'WRITE,DESTRUCTIVE' }).approvalRequiredLevels).toEqual([
      'WRITE',
      'DESTRUCTIVE',
    ]);
    // Unknown levels are dropped rather than silently widening the policy.
    expect(loadConfig({ APPROVAL_REQUIRED_LEVELS: 'WRITE,NONSENSE' }).approvalRequiredLevels).toEqual([
      'WRITE',
    ]);
  });

  it('resolves database URLs', () => {
    expect(resolveDatabasePath(':memory:')).toBe(':memory:');
    expect(resolveDatabasePath('file:./data/jarvis.db', '/srv/jarvis')).toBe('/srv/jarvis/data/jarvis.db');
    expect(resolveDatabasePath('./data/x.db', '/srv/jarvis')).toBe('/srv/jarvis/data/x.db');
  });
});

describe('secret containment', () => {
  it('publicConfig contains no credential of any kind', () => {
    const config = loadConfig({
      NVIDIA_API_KEY: 'nvapi-secret-1',
      ANTHROPIC_API_KEY: 'sk-ant-secret-2',
      OPENAI_API_KEY: 'sk-secret-3',
      BRAVE_API_KEY: 'brave-secret-4',
      TAVILY_API_KEY: 'tvly-secret-5',
      LOCAL_API_KEY: 'local-secret-6',
      JARVIS_API_TOKEN: 'jarvis-token-7',
    });

    const serialised = JSON.stringify(publicConfig(config));
    for (const secret of [
      'nvapi-secret-1',
      'sk-ant-secret-2',
      'sk-secret-3',
      'brave-secret-4',
      'tvly-secret-5',
      'local-secret-6',
      'jarvis-token-7',
    ]) {
      expect(serialised).not.toContain(secret);
    }
    expect(serialised).not.toMatch(/key|token|secret/i);
  });

  it('redact masks credential-shaped keys and values', () => {
    const redacted = redact({
      api_key: 'nvapi-abcdef123456',
      Authorization: 'Bearer sk-ant-xyz789012',
      nested: { password: 'hunter2', note: 'my key is sk-abcdefghijkl' },
      safe: 'ordinary text',
    }) as Record<string, any>;

    expect(redacted.api_key).toBe('[redacted]');
    expect(redacted.Authorization).toBe('[redacted]');
    expect(redacted.nested.password).toBe('[redacted]');
    expect(redacted.nested.note).toBe('my key is sk-***');
    expect(redacted.safe).toBe('ordinary text');
  });
});

describe('repository hygiene', () => {
  it('.env.example documents every configuration key the loader reads', () => {
    const example = readFileSync(join(repoRoot(), '.env.example'), 'utf8');
    for (const key of [
      'MODEL_PROVIDER',
      'NVIDIA_API_KEY',
      'NVIDIA_BASE_URL',
      'NVIDIA_MODEL',
      'ANTHROPIC_API_KEY',
      'ANTHROPIC_MODEL',
      'OPENAI_API_KEY',
      'OPENAI_MODEL',
      'STT_PROVIDER',
      'TTS_PROVIDER',
      'IMAGE_PROVIDER',
      'VIDEO_PROVIDER',
      'DATABASE_URL',
      'JARVIS_API_TOKEN',
      'APPROVAL_REQUIRED_LEVELS',
    ]) {
      expect(example, key).toContain(`${key}=`);
    }
  });

  it('.env.example carries no real credentials', () => {
    const example = readFileSync(join(repoRoot(), '.env.example'), 'utf8');
    expect(example).not.toMatch(/=\s*nvapi-[A-Za-z0-9_-]{10,}/);
    expect(example).not.toMatch(/=\s*sk-[A-Za-z0-9_-]{10,}/);
  });

  it('.gitignore excludes .env and the database', () => {
    const ignore = readFileSync(join(repoRoot(), '.gitignore'), 'utf8');
    expect(ignore).toMatch(/^\.env$/m);
    expect(ignore).toMatch(/\*\.db/);
    expect(ignore).toMatch(/node_modules/);
  });
});
