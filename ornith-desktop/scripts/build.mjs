/** Production build: bundle the Electron entry points, then build the renderer. */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import { buildElectron } from './esbuild-electron.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

await buildElectron({ minify: true });
await build({ root, configFile: path.join(root, 'vite.config.ts') });

console.log('\nBuilt renderer -> dist/  and main process -> dist-electron/');
