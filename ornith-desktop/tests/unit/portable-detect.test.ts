import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  detectPortable,
  ensurePortableDirs,
  findMarkerRoot,
  type DetectFs,
} from '../../electron/portable/detect';
import { probeWritable, readVolumeStats } from '../../electron/portable/volume';
import { PORTABLE_MARKER, resolveLayout } from '../../shared/portable';

/** A filesystem made of a set of paths, so detection can be tested without one. */
function fakeFs(files: Record<string, string>): DetectFs {
  return {
    exists: (target) => Object.prototype.hasOwnProperty.call(files, target),
    readText: (target) => {
      if (!Object.prototype.hasOwnProperty.call(files, target)) throw new Error('ENOENT');
      return files[target];
    },
  };
}

describe('findMarkerRoot', () => {
  it('finds a marker sitting in the start directory itself', () => {
    const fs = fakeFs({ [path.join('/mnt/stick', PORTABLE_MARKER)]: '{}' });
    expect(findMarkerRoot('/mnt/stick', fs.exists)).toBe('/mnt/stick');
  });

  it('climbs out of a packaged macOS bundle to the root that holds it', () => {
    const root = '/Volumes/ORNITH/Ornith';
    const exeDir = path.join(root, 'app/mac/Ornith Portable.app/Contents/MacOS');
    const fs = fakeFs({ [path.join(root, PORTABLE_MARKER)]: '{}' });

    expect(findMarkerRoot(exeDir, fs.exists)).toBe(root);
  });

  it('returns null when nothing above the start directory is provisioned', () => {
    expect(findMarkerRoot('/home/someone/projects/app', fakeFs({}).exists)).toBeNull();
  });

  it('stops climbing at the configured depth rather than wandering to /', () => {
    const root = '/a';
    const deep = '/a/b/c/d/e/f/g/h/i/j/k';
    const fs = fakeFs({ [path.join(root, PORTABLE_MARKER)]: '{}' });

    expect(findMarkerRoot(deep, fs.exists, 2)).toBeNull();
    expect(findMarkerRoot(deep, fs.exists, 20)).toBe(root);
  });
});

describe('detectPortable', () => {
  const marker = (root: string, body: unknown) =>
    fakeFs({ [path.join(root, PORTABLE_MARKER)]: JSON.stringify(body) });

  it('is not portable for an ordinary installed app', () => {
    const context = detectPortable({ startDir: '/Applications', env: {}, fs: fakeFs({}) });
    expect(context.portable).toBe(false);
    expect(context.root).toBeNull();
    expect(context.layout).toBeNull();
  });

  it('honours ORNITH_PORTABLE=0 as an escape hatch, marker or not', () => {
    const context = detectPortable({
      startDir: '/mnt/stick',
      env: { ORNITH_PORTABLE: '0' },
      fs: marker('/mnt/stick', {}),
    });

    expect(context.portable).toBe(false);
    expect(context.reason).toMatch(/ORNITH_PORTABLE=0/);
  });

  it('accepts an explicit root even before a marker has been written', () => {
    const context = detectPortable({
      startDir: '/somewhere/else',
      env: { ORNITH_PORTABLE_ROOT: '/mnt/stick' },
      fs: fakeFs({}),
    });

    expect(context.portable).toBe(true);
    expect(context.root).toBe('/mnt/stick');
    expect(context.layout?.dataDir).toBe(path.join('/mnt/stick', 'data'));
  });

  it('prefers the explicit root over a marker found by walking up', () => {
    const context = detectPortable({
      startDir: '/mnt/stick',
      env: { ORNITH_PORTABLE_ROOT: '/mnt/other' },
      fs: marker('/mnt/stick', {}),
    });

    expect(context.root).toBe('/mnt/other');
  });

  it('reads directory overrides out of the marker', () => {
    const context = detectPortable({
      startDir: '/mnt/stick',
      env: {},
      fs: marker('/mnt/stick', { label: 'Field kit', directories: { models: 'weights' } }),
    });

    expect(context.manifest.label).toBe('Field kit');
    expect(context.layout?.modelsDir).toBe(path.join('/mnt/stick', 'weights'));
  });

  it('still adopts the root when the marker is unparseable', () => {
    const context = detectPortable({
      startDir: '/mnt/stick',
      env: {},
      fs: fakeFs({ [path.join('/mnt/stick', PORTABLE_MARKER)]: '{ truncated' }),
    });

    expect(context.portable).toBe(true);
    expect(context.layout?.dataDir).toBe(path.join('/mnt/stick', 'data'));
  });

  it('refuses a drive written by a newer Ornith rather than corrupting it', () => {
    const context = detectPortable({
      startDir: '/mnt/stick',
      env: {},
      fs: marker('/mnt/stick', { layoutVersion: 99 }),
    });

    expect(context.portable).toBe(false);
    expect(context.layout).toBeNull();
    expect(context.reason).toMatch(/v99/);
    expect(context.reason).toMatch(/Update Ornith/);
  });
});

describe('ensurePortableDirs', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-portable-'));
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('creates everything the app writes into', () => {
    const layout = resolveLayout(root);
    ensurePortableDirs(layout);

    for (const dir of [
      layout.dataDir,
      layout.logsDir,
      layout.modelsDir,
      layout.runtimeHomeDir,
      layout.runtimeTmpDir,
    ]) {
      expect(existsSync(dir), dir).toBe(true);
    }
  });

  it('leaves runtime/ and app/ to the provisioner, so a half-built drive still looks half-built', () => {
    const layout = resolveLayout(root);
    ensurePortableDirs(layout);

    expect(existsSync(layout.runtimeDir)).toBe(false);
    expect(existsSync(layout.appDir)).toBe(false);
  });

  it('is safe to run against an already-provisioned drive', () => {
    const layout = resolveLayout(root);
    ensurePortableDirs(layout);
    writeFileSync(path.join(layout.dataDir, 'ornith.db'), 'existing');

    expect(() => ensurePortableDirs(layout)).not.toThrow();
    expect(existsSync(path.join(layout.dataDir, 'ornith.db'))).toBe(true);
  });
});

describe('volume probing', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-volume-'));
  });

  afterEach(() => {
    try {
      chmodSync(root, 0o700);
    } catch {
      /* already writable */
    }
    rmSync(root, { recursive: true, force: true });
  });

  it('reports free and total bytes for the filesystem holding the directory', () => {
    const stats = readVolumeStats(root);

    expect(stats).not.toBeNull();
    expect(stats!.totalBytes).toBeGreaterThan(0);
    expect(stats!.freeBytes).toBeGreaterThanOrEqual(0);
    expect(stats!.freeBytes).toBeLessThanOrEqual(stats!.totalBytes);
    expect(stats!.writable).toBe(true);
  });

  it('returns null rather than a guess when the directory does not exist', () => {
    expect(readVolumeStats(path.join(root, 'no-such-dir'))).toBeNull();
  });

  it('leaves no probe file behind', () => {
    expect(probeWritable(root)).toBe(true);
    expect(existsSync(root)).toBe(true);
    // The probe name is namespaced by pid; nothing of ours should survive it.
    const leftovers = readdirSync(root);
    expect(leftovers.filter((name) => name.startsWith('.ornith-write-probe'))).toEqual([]);
  });

  it('detects a directory it cannot write to', () => {
    // Running as root defeats permission bits entirely, which is the CI case.
    const readOnly = path.join(root, 'locked');
    mkdirSync(readOnly);
    chmodSync(readOnly, 0o500);

    const denied = !probeWritable(readOnly);
    chmodSync(readOnly, 0o700);

    if (process.getuid?.() === 0) {
      expect(denied).toBe(false); // root writes anywhere; nothing to assert
    } else {
      expect(denied).toBe(true);
    }
  });
});
