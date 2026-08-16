import { randomUUID, randomBytes } from 'node:crypto';

/** Prefixed, sortable-ish identifier: `msg_2b7f...`. */
export function id(prefix: string): string {
  return `${prefix}_${randomUUID().replace(/-/g, '').slice(0, 20)}`;
}

export function token(bytes = 32): string {
  return randomBytes(bytes).toString('hex');
}

export function now(): string {
  return new Date().toISOString();
}

export function secondsFromNow(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}

/** Case/word-insensitive token set used by the memory search scorer. */
export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word.length > 2);
}

export class JarvisError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(message: string, options: { status?: number; code?: string } = {}) {
    super(message);
    this.name = 'JarvisError';
    this.status = options.status ?? 500;
    this.code = options.code ?? 'internal_error';
  }
}

const SECRET_KEY_PATTERN = /(api[_-]?key|token|secret|password|authorization|bearer)/i;

/**
 * Recursively mask anything that looks like a credential.
 * Applied to every audit-log payload and every error surfaced to the client.
 */
export function redact<T>(value: T, depth = 0): T {
  if (depth > 6) return '[depth-limit]' as unknown as T;
  if (typeof value === 'string') {
    return value.replace(/\b(nvapi-|sk-|sk-ant-)[A-Za-z0-9_\-]{6,}/g, '$1***') as unknown as T;
  }
  if (Array.isArray(value)) return value.map((item) => redact(item, depth + 1)) as unknown as T;
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, inner] of Object.entries(value as Record<string, unknown>)) {
      out[key] = SECRET_KEY_PATTERN.test(key) ? '[redacted]' : redact(inner, depth + 1);
    }
    return out as unknown as T;
  }
  return value;
}

/** `fetch` with a timeout, used by every outbound provider call. */
export async function fetchWithTimeout(
  url: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<Response> {
  const { timeoutMs = 60_000, ...rest } = init;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const externalSignal = rest.signal;
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener('abort', () => controller.abort(), { once: true });
  }
  try {
    return await fetch(url, { ...rest, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

/** Host of a URL, for status display. Never returns the full URL (may carry keys). */
export function safeHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url ? 'invalid-url' : '';
  }
}
