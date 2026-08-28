/**
 * Stands in for the Ollama binary a provisioned drive carries.
 *
 * It speaks the same wire protocol as the integration stub, binds the port it
 * is handed through `OLLAMA_HOST`, and records the environment it was spawned
 * with — which is how the E2E test checks that the model store, the scratch
 * directory and `HOME` were all redirected onto the drive.
 *
 * Launched through a shim placed at `<root>/runtime/<platform>-<arch>/ollama`,
 * so this file can keep its relative import of the stub.
 */
import { writeFileSync } from 'node:fs';
import path from 'node:path';

import { startStubOllama } from '../integration/stubOllama.ts';

const host = process.env.OLLAMA_HOST ?? '127.0.0.1:11434';
const port = Number(host.slice(host.lastIndexOf(':') + 1));

const modelsDir = process.env.OLLAMA_MODELS;
if (!modelsDir) {
  console.error('fake-ollama: OLLAMA_MODELS was not set');
  process.exit(2);
}

writeFileSync(
  path.join(modelsDir, 'spawn-env.json'),
  `${JSON.stringify(
    {
      argv: process.argv.slice(2),
      cwd: process.cwd(),
      OLLAMA_MODELS: modelsDir,
      OLLAMA_HOST: process.env.OLLAMA_HOST,
      OLLAMA_TMPDIR: process.env.OLLAMA_TMPDIR,
      OLLAMA_ORIGINS: process.env.OLLAMA_ORIGINS,
      HOME: process.env.HOME,
    },
    null,
    2,
  )}\n`,
);

await startStubOllama({ port });

// Stay up until signalled, the way `ollama serve` does. Exiting on SIGTERM is
// what lets the supervisor's graceful stop be observed rather than assumed.
process.on('SIGTERM', () => process.exit(0));
setInterval(() => {}, 1 << 30);
