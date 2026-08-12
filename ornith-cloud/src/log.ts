/**
 * Structured logging. Never logs conversation content, tokens, keys, or headers.
 */
type Level = 'error' | 'warn' | 'info' | 'debug';

const ORDER: Record<Level, number> = { error: 0, warn: 1, info: 2, debug: 3 };

let threshold: Level = 'info';

export function setLogLevel(level: Level): void {
  threshold = level;
}

/** Keys that must never appear in a log line, whatever the caller passes. */
const FORBIDDEN = /^(authorization|token|apikey|api_key|key|secret|password|cookie)$/i;

export function redact(fields: Record<string, unknown>): Record<string, unknown> {
  const safe: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(fields)) {
    if (FORBIDDEN.test(key)) {
      safe[key] = '[redacted]';
      continue;
    }
    // A bearer token pasted into a message would otherwise land in the log.
    if (typeof value === 'string' && /^Bearer\s+\S+/i.test(value)) {
      safe[key] = '[redacted]';
      continue;
    }
    safe[key] = value;
  }
  return safe;
}

function write(level: Level, event: string, fields: Record<string, unknown> = {}): void {
  if (ORDER[level] > ORDER[threshold]) return;
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    event,
    ...redact(fields),
  });
  // eslint-disable-next-line no-console
  console[level === 'debug' ? 'log' : level](line);
}

export const log = {
  error: (event: string, fields?: Record<string, unknown>) => write('error', event, fields),
  warn: (event: string, fields?: Record<string, unknown>) => write('warn', event, fields),
  info: (event: string, fields?: Record<string, unknown>) => write('info', event, fields),
  debug: (event: string, fields?: Record<string, unknown>) => write('debug', event, fields),
};
