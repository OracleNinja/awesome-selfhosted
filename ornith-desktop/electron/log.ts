import { appendFileSync, mkdirSync, renameSync, statSync } from 'node:fs';
import path from 'node:path';

type Level = 'error' | 'warn' | 'info' | 'debug';

const ORDER: Record<Level, number> = { error: 0, warn: 1, info: 2, debug: 3 };
const MAX_BYTES = 5 * 1024 * 1024;

let logFile: string | null = null;
let threshold: Level = process.argv.includes('--verbose') ? 'debug' : 'info';

export function initLogger(dir: string): string {
  mkdirSync(dir, { recursive: true });
  logFile = path.join(dir, 'main.log');
  return logFile;
}

export function setLogLevel(level: Level): void {
  threshold = level;
}

function rotateIfNeeded(file: string): void {
  try {
    if (statSync(file).size < MAX_BYTES) return;
    for (let i = 2; i >= 1; i -= 1) {
      try {
        renameSync(`${file}.${i}`, `${file}.${i + 1}`);
      } catch {
        /* nothing to rotate at this index */
      }
    }
    renameSync(file, `${file}.1`);
  } catch {
    /* file does not exist yet */
  }
}

/**
 * Structured JSON lines. Conversation text is never logged — the premise of
 * this app is that the user's content stays on their machine.
 */
function write(level: Level, event: string, fields: Record<string, unknown> = {}): void {
  if (ORDER[level] > ORDER[threshold]) return;

  const line = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    event,
    ...fields,
  });

  if (process.env.NODE_ENV !== 'test') {
    // eslint-disable-next-line no-console
    console[level === 'debug' ? 'log' : level](line);
  }

  if (!logFile) return;
  try {
    rotateIfNeeded(logFile);
    appendFileSync(logFile, `${line}\n`, 'utf8');
  } catch {
    /* logging must never break the app */
  }
}

export const log = {
  error: (event: string, fields?: Record<string, unknown>) => write('error', event, fields),
  warn: (event: string, fields?: Record<string, unknown>) => write('warn', event, fields),
  info: (event: string, fields?: Record<string, unknown>) => write('info', event, fields),
  debug: (event: string, fields?: Record<string, unknown>) => write('debug', event, fields),
};
