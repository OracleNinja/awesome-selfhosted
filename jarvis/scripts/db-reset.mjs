#!/usr/bin/env node
/**
 * Delete the JARVIS database so the next start recreates it from migrations.
 * Destructive: it removes conversations, memories, tasks and the audit log.
 */
import { existsSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));

let databaseUrl = 'file:./data/jarvis.db';
const envPath = join(root, '.env');
if (existsSync(envPath)) {
  const match = readFileSync(envPath, 'utf8').match(/^DATABASE_URL=(.*)$/m);
  if (match?.[1]) databaseUrl = match[1].trim();
}

const raw = databaseUrl.startsWith('file:') ? databaseUrl.slice(5) : databaseUrl;
if (raw === ':memory:') {
  console.log('  DATABASE_URL is :memory: — nothing on disk to remove.');
  process.exit(0);
}

const path = resolve(root, raw);
let removed = 0;
for (const suffix of ['', '-wal', '-shm', '-journal']) {
  const file = `${path}${suffix}`;
  if (existsSync(file)) {
    rmSync(file);
    removed += 1;
  }
}
console.log(removed > 0 ? `  removed ${removed} file(s) for ${path}` : `  no database found at ${path}`);
