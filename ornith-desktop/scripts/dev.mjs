/**
 * Dev runner: builds the Electron entry points, starts Vite, then launches
 * Electron pointed at the dev server. Ctrl+C or quitting the app tears down both.
 */
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import electronPath from 'electron';
import { buildElectron } from './esbuild-electron.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

await buildElectron();

const server = await createServer({ root, configFile: path.join(root, 'vite.config.ts') });
await server.listen();

const url = server.resolvedUrls?.local?.[0];
if (!url) {
  await server.close();
  throw new Error('Vite did not report a local dev URL.');
}
server.printUrls();

const electron = spawn(electronPath, [root], {
  stdio: 'inherit',
  env: { ...process.env, VITE_DEV_SERVER_URL: url },
});

let shuttingDown = false;
async function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  await server.close().catch(() => {});
  process.exit(code);
}

electron.on('close', (code) => void shutdown(code ?? 0));
process.on('SIGINT', () => {
  electron.kill();
  void shutdown(0);
});
process.on('SIGTERM', () => {
  electron.kill();
  void shutdown(0);
});
