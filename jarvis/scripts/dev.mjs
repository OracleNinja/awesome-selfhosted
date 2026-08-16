#!/usr/bin/env node
/**
 * One-command development: `npm run dev`.
 *
 * Starts the API server (tsx watch) and the Vite dev server together, prefixes
 * their output, and shuts both down on Ctrl-C.
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));

const processes = [
  {
    name: 'server',
    colour: '[36m',
    command: 'npx',
    args: ['tsx', 'watch', '--clear-screen=false', 'apps/server/src/index.ts'],
  },
  {
    name: 'web   ',
    colour: '[35m',
    command: 'npm',
    args: ['run', 'dev', '--workspace', '@jarvis/web'],
  },
];

const children = [];
let shuttingDown = false;

for (const definition of processes) {
  const child = spawn(definition.command, definition.args, {
    cwd: root,
    env: process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  const prefix = `${definition.colour}[${definition.name}][0m `;
  const relay = (stream, target) => {
    let buffer = '';
    stream.on('data', (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) target.write(`${prefix}${line}\n`);
    });
  };
  relay(child.stdout, process.stdout);
  relay(child.stderr, process.stderr);

  child.on('exit', (code) => {
    if (shuttingDown) return;
    console.error(`${prefix}exited with code ${code} — shutting down.`);
    shutdown(code ?? 1);
  });

  children.push(child);
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) child.kill('SIGTERM');
  setTimeout(() => process.exit(code), 300);
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

console.log('\n  JARVIS development mode');
console.log('  API  http://localhost:8787');
console.log('  UI   http://localhost:5173   ← open this one\n');
