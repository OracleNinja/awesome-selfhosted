import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  PORTABLE_LAYOUT_VERSION,
  SUPPORTED_RUNTIME_SLUGS,
  resolveLayout,
  runtimeBinaryName,
} from '../../shared/portable';
import { detectPortable } from '../../electron/portable/detect';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const script = path.join(appRoot, 'scripts', 'provision-portable.mjs');

function provision(args: string[]): string {
  return execFileSync(process.execPath, [script, ...args], {
    encoding: 'utf8',
    cwd: appRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

describe('provision-portable', () => {
  let workspace: string;
  let root: string;

  beforeEach(() => {
    workspace = mkdtempSync(path.join(tmpdir(), 'ornith-provision-'));
    root = path.join(workspace, 'drive', 'Ornith');
  });

  afterEach(() => rmSync(workspace, { recursive: true, force: true }));

  it('lays out a tree the app then recognises as portable', () => {
    provision(['--dest', root, '--label', 'Field kit']);

    const layout = resolveLayout(root);
    for (const dir of [layout.dataDir, layout.logsDir, layout.modelsDir, layout.runtimeDir, layout.appDir]) {
      expect(existsSync(dir), dir).toBe(true);
    }

    // The real detector, on the real tree: provisioner and app agree or neither works.
    const context = detectPortable({ startDir: root, env: {} });
    expect(context.portable).toBe(true);
    expect(context.root).toBe(root);
    expect(context.manifest.label).toBe('Field kit');
    expect(context.layout?.dataDir).toBe(layout.dataDir);
  });

  it('writes a runtime slot for every platform the drive can serve', () => {
    provision(['--dest', root]);
    const layout = resolveLayout(root);

    for (const slug of SUPPORTED_RUNTIME_SLUGS) {
      expect(existsSync(path.join(layout.runtimeDir, slug)), slug).toBe(true);
    }
  });

  it('writes a launcher for each platform', () => {
    provision(['--dest', root]);

    for (const launcher of ['Ornith.command', 'ornith.sh', 'Ornith.bat']) {
      expect(existsSync(path.join(root, launcher)), launcher).toBe(true);
    }
  });

  it('names the runtime binaries the app will actually look for', () => {
    const output = provision(['--dest', root]);
    const layout = resolveLayout(root);

    expect(output).toContain(path.join(layout.runtimeDir, 'darwin-arm64', runtimeBinaryName('darwin')));
    expect(output).toContain(path.join(layout.runtimeDir, 'win32-x64', runtimeBinaryName('win32')));
  });

  it('leaves existing user data alone when re-run', () => {
    provision(['--dest', root]);

    const layout = resolveLayout(root);
    writeFileSync(layout.dbPath, 'pretend database');
    writeFileSync(layout.settingsPath, '{"schemaVersion":1}');

    provision(['--dest', root]);

    expect(readFileSync(layout.dbPath, 'utf8')).toBe('pretend database');
    expect(readFileSync(layout.settingsPath, 'utf8')).toBe('{"schemaVersion":1}');
  });

  it('refuses a drive written by a newer layout rather than reformatting it', () => {
    provision(['--dest', root]);

    const layout = resolveLayout(root);
    const manifest = JSON.parse(readFileSync(layout.markerPath, 'utf8')) as Record<string, unknown>;
    writeFileSync(
      layout.markerPath,
      JSON.stringify({ ...manifest, layoutVersion: PORTABLE_LAYOUT_VERSION + 1 }),
    );

    expect(() => provision(['--dest', root])).toThrow();
    // And the marker it refused to touch is unchanged.
    const after = JSON.parse(readFileSync(layout.markerPath, 'utf8')) as { layoutVersion: number };
    expect(after.layoutVersion).toBe(PORTABLE_LAYOUT_VERSION + 1);
  });

  it('copies a model store without touching the source', () => {
    const source = path.join(workspace, 'source-models');
    mkdirSync(path.join(source, 'blobs'), { recursive: true });
    writeFileSync(path.join(source, 'blobs', 'sha256-abc'), 'weights');

    provision(['--dest', root, '--copy-models', source]);

    const layout = resolveLayout(root);
    expect(readFileSync(path.join(layout.modelsDir, 'blobs', 'sha256-abc'), 'utf8')).toBe('weights');
    // SPEC §19 ground rule 1: the user's model store is not ours to move.
    expect(readFileSync(path.join(source, 'blobs', 'sha256-abc'), 'utf8')).toBe('weights');
  });

  it('refuses a source that does not exist instead of creating an empty store', () => {
    expect(() => provision(['--dest', root, '--copy-models', path.join(workspace, 'nope')])).toThrow();
  });

  it('exits with usage when given no destination', () => {
    expect(() => provision([])).toThrow();
  });
});
