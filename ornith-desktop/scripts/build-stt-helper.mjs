/**
 * Compiles the on-device speech helper.
 *
 * macOS only, and requires Xcode Command Line Tools. Exits 0 with a message on
 * other platforms so it can sit in a cross-platform build without breaking it —
 * the app degrades to "speech helper unavailable" rather than failing to start.
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'native', 'OrnithSTT.swift');
const outDir = path.join(root, 'native', 'build');
const outFile = path.join(outDir, 'ornith-stt');

if (process.platform !== 'darwin') {
  console.log('[build:stt] Skipping: the speech helper is macOS-only.');
  process.exit(0);
}

try {
  execFileSync('xcrun', ['--find', 'swiftc'], { stdio: 'ignore' });
} catch {
  console.error(
    '[build:stt] swiftc not found. Install Xcode Command Line Tools:\n' +
      '  xcode-select --install',
  );
  process.exit(1);
}

if (!existsSync(source)) {
  console.error(`[build:stt] Source missing: ${source}`);
  process.exit(1);
}

mkdirSync(outDir, { recursive: true });

execFileSync(
  'xcrun',
  ['swiftc', '-O', '-framework', 'Speech', '-framework', 'Foundation', source, '-o', outFile],
  { stdio: 'inherit' },
);

console.log(`[build:stt] Built ${outFile}`);
