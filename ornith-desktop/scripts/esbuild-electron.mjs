import esbuild from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/**
 * The main process ships as ESM (package.json has "type": "module"), but the
 * preload script must be CommonJS — Electron loads preloads with `require`,
 * and the .cjs extension is what makes that unambiguous under a module package.
 */
const targets = [
  {
    entryPoints: [path.join(root, 'electron/main.ts')],
    outfile: path.join(root, 'dist-electron/main.js'),
    format: 'esm',
  },
  {
    entryPoints: [path.join(root, 'electron/preload.ts')],
    outfile: path.join(root, 'dist-electron/preload.cjs'),
    format: 'cjs',
  },
];

export async function buildElectron({ minify = false } = {}) {
  await Promise.all(
    targets.map((target) =>
      esbuild.build({
        ...target,
        bundle: true,
        platform: 'node',
        target: 'node22',
        sourcemap: true,
        minify,
        // Electron's own module is provided at runtime, never bundled.
        external: ['electron'],
        logLevel: 'warning',
      }),
    ),
  );
}
