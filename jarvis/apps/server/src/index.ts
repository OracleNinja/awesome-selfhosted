/**
 * JARVIS server entrypoint.
 *
 * Loads configuration, builds the system, prints an honest startup report —
 * including which capabilities are NOT configured — and listens.
 */
import { join } from 'node:path';
import { existsSync } from 'node:fs';
import { createJarvis } from '@jarvis/core';
import { JARVIS_VERSION, loadConfig, loadDotEnv, repoRoot } from '@jarvis/shared';
import { startJarvisServer } from './server.ts';

async function main(): Promise<void> {
  loadDotEnv();
  const config = loadConfig();
  const jarvis = createJarvis({ config });
  // Periodic housekeeping (approval expiry) runs only in the server process.
  jarvis.startBackgroundTasks();
  const status = jarvis.status();

  const webDir = join(repoRoot(), 'apps', 'web', 'dist');
  const options = existsSync(webDir) ? { jarvis, webDir } : { jarvis };
  const { port, host } = await startJarvisServer(options);

  const unavailable = status.providers.filter((provider) => !provider.available);
  const available = status.providers.filter((provider) => provider.available);

  console.log(`\n  JARVIS ${JARVIS_VERSION}`);
  console.log(`  ${'─'.repeat(52)}`);
  console.log(`  listening        http://${host === '0.0.0.0' ? 'localhost' : host}:${port}`);
  console.log(`  auth             ${config.apiToken ? 'bearer token required' : 'local mode (loopback only, no token set)'}`);
  console.log(`  model provider   ${status.activeModelProvider} · ${status.activeModel}`);
  console.log(`  database         schema v${status.database.schemaVersion}, ${status.database.tables.length} tables`);
  console.log(`  tools            ${status.tools.total} registered, ${status.tools.requiringApproval} approval-gated`);
  console.log(`  agents           ${status.agents.join(', ')}`);
  console.log(`  workspace        ${config.workspaceDir}`);
  console.log(`  web app          ${existsSync(webDir) ? 'served from apps/web/dist' : 'not built — run `npm run dev` or `npm run build:web`'}`);

  if (available.length > 0) {
    console.log(`\n  configured:`);
    for (const provider of available) {
      console.log(`    ✓ ${provider.id}${provider.model ? ` (${provider.model})` : ''}`);
    }
  }
  if (unavailable.length > 0) {
    console.log(`\n  not configured — these capabilities are unavailable and JARVIS will say so:`);
    for (const provider of unavailable) {
      console.log(`    · ${provider.id}: ${provider.reason ?? 'not configured'}`);
    }
  }
  if (status.charterErrors.length > 0) {
    console.log(`\n  agent charter problems:`);
    for (const error of status.charterErrors) console.log(`    ! ${error}`);
  }
  console.log('');

  const shutdown = (signal: string) => {
    console.log(`\n  ${signal} received — shutting down.`);
    jarvis.close();
    process.exit(0);
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

main().catch((error) => {
  console.error('JARVIS failed to start:', error);
  process.exit(1);
});
