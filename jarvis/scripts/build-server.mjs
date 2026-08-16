#!/usr/bin/env node
/**
 * Production build for the server.
 *
 * Bundles the workspace packages into a single ESM file so deployment is
 * `node apps/server/dist/server.js` with only the native SQLite binding as an
 * external dependency.
 */
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { statSync } from 'node:fs';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const outfile = join(root, 'apps/server/dist/server.js');

const result = await build({
  entryPoints: [join(root, 'apps/server/src/index.ts')],
  outfile,
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'esm',
  sourcemap: true,
  minify: false, // readable stack traces matter more than bytes on a server
  // Native module: cannot be bundled, and does not need to be.
  external: ['better-sqlite3'],
  banner: {
    // Bundled ESM still needs CommonJS interop for the native binding.
    js: [
      "import { createRequire as __jarvisCreateRequire } from 'node:module';",
      'const require = __jarvisCreateRequire(import.meta.url);',
    ].join('\n'),
  },
  logLevel: 'info',
});

if (result.errors.length > 0) process.exit(1);

const { size } = statSync(outfile);
console.log(`  built apps/server/dist/server.js (${(size / 1024).toFixed(0)} kB)`);
