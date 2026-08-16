/**
 * Configuration loading.
 *
 * Everything JARVIS knows about credentials lives here and is read exactly
 * once, server-side. `publicConfig()` is the only shape that ever reaches the
 * browser, and it is built by explicit allow-list — never by deleting keys
 * from the private config.
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { RISK_LEVELS, type RiskLevel } from './types.ts';

export type ModelProviderId = 'nvidia' | 'anthropic' | 'openai' | 'local';
export type VoiceProviderId = 'browser' | 'nvidia';
export type MediaProviderId = 'none' | 'nvidia';
export type SearchProviderId = 'none' | 'searxng' | 'brave' | 'tavily';

export interface ModelEndpointConfig {
  apiKey: string;
  baseUrl: string;
  model: string;
}

/**
 * What a configured model can do.
 *
 * Declared per provider because capability is a property of the *model*, not
 * the vendor — the same provider serves vision and non-vision models. Mirrored
 * in @jarvis/providers as ModelCapability; declared here because it is
 * configuration.
 */
export interface ModelCapabilityConfig {
  toolUse: boolean;
  vision: boolean;
  structuredOutput: boolean;
  streaming: boolean;
  coding: boolean;
  longContext: boolean;
}

export interface JarvisConfig {
  port: number;
  apiToken: string;
  databaseUrl: string;
  workspaceDir: string;
  modelProvider: ModelProviderId;

  nvidia: ModelEndpointConfig;
  anthropic: ModelEndpointConfig;
  openai: ModelEndpointConfig;
  local: ModelEndpointConfig;

  stt: { provider: VoiceProviderId; url: string; model: string };
  tts: { provider: VoiceProviderId; url: string; model: string; voice: string };

  image: { provider: MediaProviderId; url: string; model: string };
  imageEdit: { provider: MediaProviderId; url: string; model: string };
  video: { provider: MediaProviderId; url: string; model: string };
  vision: { provider: MediaProviderId; model: string };

  search: {
    provider: SearchProviderId;
    searxngUrl: string;
    braveApiKey: string;
    tavilyApiKey: string;
  };

  approvalRequiredLevels: RiskLevel[];
  approvalTimeoutSeconds: number;

  /** Declared capabilities per provider, for capability-based routing. */
  capabilities: Record<ModelProviderId, ModelCapabilityConfig>;
  /** Deterministic order tried when the default provider cannot serve a request. */
  routingFallback: ModelProviderId[];

  /** Configured MCP servers. Only these may supply capabilities. */
  mcpServers: McpServerConfig[];
}

/**
 * A configured MCP server.
 *
 * Servers are declared here and nowhere else: there is no interface for a
 * remote server to register itself, because "connect to anything" is how a
 * capability system becomes an attack surface.
 */
export interface McpServerConfig {
  /** JARVIS-assigned id. Forms the capability namespace; never taken from the server. */
  id: string;
  /** Stdio is the only transport in v0.2. */
  transport: 'stdio';
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
  /**
   * Risk floor for every tool this server offers.
   *
   * JARVIS decides this, not the server. Defaults to EXTERNAL_ACTION: a remote
   * capability reaches outside the runtime by definition, so it is
   * approval-gated unless an operator deliberately lowers it.
   */
  riskFloor: RiskLevel;
  timeoutMs: number;
}

/** The subset of configuration the browser is allowed to know about. */
export interface PublicConfig {
  modelProvider: ModelProviderId;
  sttProvider: VoiceProviderId;
  ttsProvider: VoiceProviderId;
  imageProvider: MediaProviderId;
  imageEditProvider: MediaProviderId;
  videoProvider: MediaProviderId;
  visionProvider: MediaProviderId;
  searchProvider: SearchProviderId;
  approvalRequiredLevels: RiskLevel[];
  version: string;
}

export const JARVIS_VERSION = '0.1.0';

/** Minimal `.env` parser — avoids a dependency for ~20 lines of work. */
export function parseEnvFile(contents: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const rawLine of contents.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length > 1) ||
      (value.startsWith("'") && value.endsWith("'") && value.length > 1)
    ) {
      value = value.slice(1, -1);
    }
    if (key) out[key] = value;
  }
  return out;
}

/** Load `.env` into process.env without overwriting anything already set. */
export function loadDotEnv(cwd: string = repoRoot()): void {
  const path = join(cwd, '.env');
  if (!existsSync(path)) return;
  const parsed = parseEnvFile(readFileSync(path, 'utf8'));
  for (const [key, value] of Object.entries(parsed)) {
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

/** Repo root, resolved relative to this file so cwd never matters. */
export function repoRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
}

function str(env: NodeJS.ProcessEnv, key: string, fallback = ''): string {
  const value = env[key];
  return value === undefined || value === '' ? fallback : value;
}

function num(env: NodeJS.ProcessEnv, key: string, fallback: number): number {
  const parsed = Number(env[key]);
  return Number.isFinite(parsed) && env[key] !== '' && env[key] !== undefined ? parsed : fallback;
}

function oneOf<T extends string>(
  env: NodeJS.ProcessEnv,
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const value = str(env, key).toLowerCase() as T;
  return allowed.includes(value) ? value : fallback;
}

/** Turn `file:./data/jarvis.db`, `./data/jarvis.db` or `:memory:` into a path. */
export function resolveDatabasePath(databaseUrl: string, root = repoRoot()): string {
  const raw = databaseUrl.startsWith('file:') ? databaseUrl.slice('file:'.length) : databaseUrl;
  if (raw === ':memory:') return ':memory:';
  return resolve(root, raw);
}

const DEFAULT_CAPABILITY_FLAGS: Record<ModelProviderId, ModelCapabilityConfig> = {
  nvidia: { toolUse: true, vision: false, structuredOutput: true, streaming: true, coding: true, longContext: false },
  anthropic: { toolUse: true, vision: true, structuredOutput: true, streaming: true, coding: true, longContext: true },
  openai: { toolUse: true, vision: true, structuredOutput: true, streaming: true, coding: true, longContext: true },
  // A local runtime could be anything; claim nothing until told otherwise.
  local: { toolUse: false, vision: false, structuredOutput: false, streaming: false, coding: false, longContext: false },
};

const CAPABILITY_KEYS: (keyof ModelCapabilityConfig)[] = [
  'toolUse',
  'vision',
  'structuredOutput',
  'streaming',
  'coding',
  'longContext',
];

/** `toolUse,vision` → a capability set. Unknown names are ignored. */
export function parseCapabilityFlags(
  raw: string,
  fallback: ModelCapabilityConfig,
): ModelCapabilityConfig {
  if (!raw.trim()) return { ...fallback };
  const declared = new Set(raw.split(',').map((entry) => entry.trim()).filter(Boolean));
  const result = { ...fallback };
  for (const key of CAPABILITY_KEYS) result[key] = declared.has(key);
  return result;
}

/**
 * Parse MCP server declarations from the environment.
 *
 * Format: `MCP_SERVERS=id:command arg arg;other:command`. Anything more
 * elaborate belongs in a config file, which is a Stage 3 concern; this keeps
 * declaration explicit and auditable in one place.
 */
export function parseMcpServers(env: NodeJS.ProcessEnv): McpServerConfig[] {
  const raw = str(env, 'MCP_SERVERS');
  if (!raw.trim()) return [];

  const servers: McpServerConfig[] = [];
  for (const entry of raw.split(';')) {
    const trimmed = entry.trim();
    if (!trimmed) continue;
    const colon = trimmed.indexOf(':');
    if (colon <= 0) continue;

    const id = trimmed.slice(0, colon).trim();
    const commandLine = trimmed.slice(colon + 1).trim();
    if (!id || !commandLine) continue;
    // The id becomes a capability namespace, so it must be a safe identifier.
    if (!/^[a-z][a-z0-9_]{0,31}$/.test(id)) continue;

    const parts = commandLine.split(/\s+/);
    const command = parts[0]!;
    const upper = id.toUpperCase();

    servers.push({
      id,
      transport: 'stdio',
      command,
      args: parts.slice(1),
      env: {},
      enabled: str(env, `MCP_${upper}_ENABLED`, 'true') !== 'false',
      riskFloor: (() => {
        const declared = str(env, `MCP_${upper}_RISK_FLOOR`, 'EXTERNAL_ACTION').toUpperCase();
        return (RISK_LEVELS as readonly string[]).includes(declared)
          ? (declared as RiskLevel)
          : 'EXTERNAL_ACTION';
      })(),
      timeoutMs: num(env, `MCP_${upper}_TIMEOUT_MS`, 30_000),
    });
  }
  return servers;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): JarvisConfig {
  const root = repoRoot();
  const approvalLevels = str(env, 'APPROVAL_REQUIRED_LEVELS', 'EXTERNAL_ACTION,DESTRUCTIVE')
    .split(',')
    .map((level) => level.trim().toUpperCase())
    .filter((level): level is RiskLevel => (RISK_LEVELS as readonly string[]).includes(level));

  return {
    port: num(env, 'PORT', 8787),
    apiToken: str(env, 'JARVIS_API_TOKEN'),
    databaseUrl: str(env, 'DATABASE_URL', 'file:./data/jarvis.db'),
    workspaceDir: resolve(root, str(env, 'JARVIS_WORKSPACE_DIR', './workspace')),
    modelProvider: oneOf(env, 'MODEL_PROVIDER', ['nvidia', 'anthropic', 'openai', 'local'], 'nvidia'),

    nvidia: {
      apiKey: str(env, 'NVIDIA_API_KEY'),
      baseUrl: str(env, 'NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1'),
      model: str(env, 'NVIDIA_MODEL', 'meta/llama-3.3-70b-instruct'),
    },
    anthropic: {
      apiKey: str(env, 'ANTHROPIC_API_KEY'),
      baseUrl: str(env, 'ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
      model: str(env, 'ANTHROPIC_MODEL', 'claude-sonnet-4-5'),
    },
    openai: {
      apiKey: str(env, 'OPENAI_API_KEY'),
      baseUrl: str(env, 'OPENAI_BASE_URL', 'https://api.openai.com/v1'),
      model: str(env, 'OPENAI_MODEL', 'gpt-4o-mini'),
    },
    local: {
      apiKey: str(env, 'LOCAL_API_KEY'),
      baseUrl: str(env, 'LOCAL_BASE_URL'),
      model: str(env, 'LOCAL_MODEL'),
    },

    stt: {
      provider: oneOf(env, 'STT_PROVIDER', ['browser', 'nvidia'], 'browser'),
      url: str(env, 'NVIDIA_STT_URL'),
      model: str(env, 'NVIDIA_STT_MODEL'),
    },
    tts: {
      provider: oneOf(env, 'TTS_PROVIDER', ['browser', 'nvidia'], 'browser'),
      url: str(env, 'NVIDIA_TTS_URL'),
      model: str(env, 'NVIDIA_TTS_MODEL'),
      voice: str(env, 'NVIDIA_TTS_VOICE'),
    },

    image: {
      provider: oneOf(env, 'IMAGE_PROVIDER', ['none', 'nvidia'], 'none'),
      url: str(env, 'NVIDIA_IMAGE_URL'),
      model: str(env, 'NVIDIA_IMAGE_MODEL'),
    },
    imageEdit: {
      provider: oneOf(env, 'IMAGE_EDIT_PROVIDER', ['none', 'nvidia'], 'none'),
      url: str(env, 'NVIDIA_IMAGE_EDIT_URL'),
      model: str(env, 'NVIDIA_IMAGE_EDIT_MODEL'),
    },
    video: {
      provider: oneOf(env, 'VIDEO_PROVIDER', ['none', 'nvidia'], 'none'),
      url: str(env, 'NVIDIA_VIDEO_URL'),
      model: str(env, 'NVIDIA_VIDEO_MODEL'),
    },
    vision: {
      provider: oneOf(env, 'VISION_PROVIDER', ['none', 'nvidia'], 'none'),
      model: str(env, 'NVIDIA_VISION_MODEL'),
    },

    search: {
      provider: oneOf(env, 'SEARCH_PROVIDER', ['none', 'searxng', 'brave', 'tavily'], 'none'),
      searxngUrl: str(env, 'SEARXNG_URL'),
      braveApiKey: str(env, 'BRAVE_API_KEY'),
      tavilyApiKey: str(env, 'TAVILY_API_KEY'),
    },

    approvalRequiredLevels: approvalLevels,
    approvalTimeoutSeconds: num(env, 'APPROVAL_TIMEOUT_SECONDS', 900),

    capabilities: {
      nvidia: parseCapabilityFlags(str(env, 'NVIDIA_CAPABILITIES'), DEFAULT_CAPABILITY_FLAGS.nvidia),
      anthropic: parseCapabilityFlags(str(env, 'ANTHROPIC_CAPABILITIES'), DEFAULT_CAPABILITY_FLAGS.anthropic),
      openai: parseCapabilityFlags(str(env, 'OPENAI_CAPABILITIES'), DEFAULT_CAPABILITY_FLAGS.openai),
      local: parseCapabilityFlags(str(env, 'LOCAL_CAPABILITIES'), DEFAULT_CAPABILITY_FLAGS.local),
    },
    routingFallback: str(env, 'MODEL_ROUTING_FALLBACK')
      .split(',')
      .map((entry) => entry.trim().toLowerCase())
      .filter((entry): entry is ModelProviderId =>
        ['nvidia', 'anthropic', 'openai', 'local'].includes(entry),
      ),

    mcpServers: parseMcpServers(env),
  };
}

/**
 * Build the browser-visible configuration.
 *
 * Written as an explicit construction rather than a redaction so that adding a
 * secret to `JarvisConfig` can never leak it by omission.
 */
export function publicConfig(config: JarvisConfig): PublicConfig {
  return {
    modelProvider: config.modelProvider,
    sttProvider: config.stt.provider,
    ttsProvider: config.tts.provider,
    imageProvider: config.image.provider,
    imageEditProvider: config.imageEdit.provider,
    videoProvider: config.video.provider,
    visionProvider: config.vision.provider,
    searchProvider: config.search.provider,
    approvalRequiredLevels: config.approvalRequiredLevels,
    version: JARVIS_VERSION,
  };
}
