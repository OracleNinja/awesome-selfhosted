import { describe, expect, it } from 'vitest';
import {
  CAPACITY_CRITICAL_BYTES,
  CAPACITY_LOW_BYTES,
  DEFAULT_MANIFEST,
  PORTABLE_LAYOUT_VERSION,
  PORTABLE_MARKER,
  classifyCapacity,
  formatBytes,
  isSafeSegment,
  isSupportedLayout,
  resolveLayout,
  runtimeBinaryName,
  runtimeBinaryPath,
  runtimeSlug,
  validateManifest,
} from '../../shared/portable';

describe('manifest validation', () => {
  it('returns defaults for junk input', () => {
    expect(validateManifest(null)).toEqual(DEFAULT_MANIFEST);
    expect(validateManifest('nonsense')).toEqual(DEFAULT_MANIFEST);
    expect(validateManifest(42)).toEqual(DEFAULT_MANIFEST);
  });

  it('keeps valid directory overrides', () => {
    const manifest = validateManifest({ directories: { data: 'state', models: 'weights' } });
    expect(manifest.directories.data).toBe('state');
    expect(manifest.directories.models).toBe('weights');
    // Untouched keys keep their defaults.
    expect(manifest.directories.runtime).toBe('runtime');
  });

  it('replaces only the invalid directory, keeping the valid ones', () => {
    const manifest = validateManifest({ directories: { data: '../../etc', models: 'weights' } });
    expect(manifest.directories.data).toBe('data');
    expect(manifest.directories.models).toBe('weights');
  });

  it('trims and caps a label', () => {
    expect(validateManifest({ label: '  Ian’s drive  ' }).label).toBe('Ian’s drive');
    expect(validateManifest({ label: 'x'.repeat(200) }).label).toHaveLength(64);
    expect(validateManifest({ label: '   ' }).label).toBe(DEFAULT_MANIFEST.label);
  });

  it('keeps an explicit layout version so an unsupported drive can be refused', () => {
    expect(validateManifest({ layoutVersion: 7 }).layoutVersion).toBe(7);
    expect(validateManifest({ layoutVersion: 1.5 }).layoutVersion).toBe(PORTABLE_LAYOUT_VERSION);
  });
});

describe('isSafeSegment', () => {
  it('accepts an ordinary directory name', () => {
    expect(isSafeSegment('data')).toBe(true);
    expect(isSafeSegment('model-store')).toBe(true);
  });

  it('rejects anything that could escape the root', () => {
    for (const bad of ['..', '.', '../etc', 'a/b', 'a\\b', 'C:', 'C:\\models', '', '  '.repeat(40)]) {
      expect(isSafeSegment(bad), bad).toBe(false);
    }
  });

  it('rejects control characters, which produce paths no tool can name', () => {
    expect(isSafeSegment('da\u0000ta')).toBe(false);
    expect(isSafeSegment('da\ttest')).toBe(false);
    expect(isSafeSegment('data\u007f')).toBe(false);
  });

  it('rejects non-strings', () => {
    expect(isSafeSegment(undefined)).toBe(false);
    expect(isSafeSegment(7)).toBe(false);
    expect(isSafeSegment({})).toBe(false);
  });
});

describe('isSupportedLayout', () => {
  it('accepts this version and older', () => {
    expect(isSupportedLayout({ ...DEFAULT_MANIFEST, layoutVersion: 1 })).toBe(true);
    expect(isSupportedLayout({ ...DEFAULT_MANIFEST, layoutVersion: 0 })).toBe(true);
  });

  it('refuses a drive written by a newer Ornith rather than guessing at it', () => {
    expect(isSupportedLayout({ ...DEFAULT_MANIFEST, layoutVersion: 2 })).toBe(false);
  });
});

describe('resolveLayout', () => {
  it('derives every path from the one root', () => {
    const layout = resolveLayout('/Volumes/ORNITH/Ornith');

    expect(layout.markerPath).toBe(`/Volumes/ORNITH/Ornith/${PORTABLE_MARKER}`);
    expect(layout.dataDir).toBe('/Volumes/ORNITH/Ornith/data');
    expect(layout.dbPath).toBe('/Volumes/ORNITH/Ornith/data/ornith.db');
    expect(layout.settingsPath).toBe('/Volumes/ORNITH/Ornith/data/settings.json');
    expect(layout.logsDir).toBe('/Volumes/ORNITH/Ornith/data/logs');
    expect(layout.modelsDir).toBe('/Volumes/ORNITH/Ornith/models');
    expect(layout.runtimeDir).toBe('/Volumes/ORNITH/Ornith/runtime');
    expect(layout.appDir).toBe('/Volumes/ORNITH/Ornith/app');
  });

  it('keeps the runtime home and scratch dirs on the drive, under data', () => {
    const layout = resolveLayout('/mnt/stick');
    expect(layout.runtimeHomeDir).toBe('/mnt/stick/data/runtime-home');
    expect(layout.runtimeTmpDir).toBe('/mnt/stick/data/runtime-tmp');
  });

  it('honours manifest directory overrides', () => {
    const manifest = validateManifest({ directories: { data: 'state' } });
    expect(resolveLayout('/mnt/stick', manifest).dataDir).toBe('/mnt/stick/state');
  });

  it('tolerates a trailing separator on the root', () => {
    expect(resolveLayout('/mnt/stick/').dataDir).toBe('/mnt/stick/data');
  });

  it('uses backslashes for a Windows drive root', () => {
    const layout = resolveLayout('E:\\Ornith');
    expect(layout.dataDir).toBe('E:\\Ornith\\data');
    expect(layout.dbPath).toBe('E:\\Ornith\\data\\ornith.db');
  });
});

describe('runtime binary resolution', () => {
  it('names the directory after platform and arch so one drive serves many machines', () => {
    expect(runtimeSlug('darwin', 'arm64')).toBe('darwin-arm64');
    expect(runtimeSlug('win32', 'x64')).toBe('win32-x64');
  });

  it('adds .exe only on Windows', () => {
    expect(runtimeBinaryName('darwin')).toBe('ollama');
    expect(runtimeBinaryName('linux')).toBe('ollama');
    expect(runtimeBinaryName('win32')).toBe('ollama.exe');
  });

  it('resolves the full path under the runtime directory', () => {
    const layout = resolveLayout('/mnt/stick');
    expect(runtimeBinaryPath(layout, 'darwin', 'arm64')).toBe(
      '/mnt/stick/runtime/darwin-arm64/ollama',
    );
    expect(runtimeBinaryPath(resolveLayout('E:\\Ornith'), 'win32', 'x64')).toBe(
      'E:\\Ornith\\runtime\\win32-x64\\ollama.exe',
    );
  });
});

describe('capacity classification', () => {
  const volume = (freeBytes: number, writable = true) => ({
    writable,
    freeBytes,
    totalBytes: 128 * 1024 ** 3,
  });

  it('treats unknown free space as fine rather than as full', () => {
    expect(classifyCapacity(null)).toBe('ok');
  });

  it('warns before writes start failing, not after', () => {
    expect(classifyCapacity(volume(40 * 1024 ** 3))).toBe('ok');
    expect(classifyCapacity(volume(CAPACITY_LOW_BYTES - 1))).toBe('low');
    expect(classifyCapacity(volume(CAPACITY_CRITICAL_BYTES - 1))).toBe('critical');
  });

  it('treats a read-only drive as critical however much room it has', () => {
    expect(classifyCapacity(volume(100 * 1024 ** 3, false))).toBe('critical');
  });
});

describe('formatBytes', () => {
  it('uses drive-sized units', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(900)).toBe('900 B');
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(5 * 1024 ** 3)).toBe('5 GB');
    expect(formatBytes(128 * 1024 ** 3)).toBe('128 GB');
  });

  it('drops the decimal once the number is big enough not to need it', () => {
    expect(formatBytes(120.4 * 1024 ** 3)).toBe('120 GB');
  });

  it('does not invent a number it does not have', () => {
    expect(formatBytes(Number.NaN)).toBe('—');
    expect(formatBytes(-1)).toBe('—');
  });
});
