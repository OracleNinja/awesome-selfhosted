import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createSettingsStore, validateSettings } from '../../electron/store/settings';
import { DEFAULT_SETTINGS } from '../../shared/defaults';

describe('validateSettings', () => {
  it('returns defaults for junk input', () => {
    expect(validateSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(validateSettings('nonsense')).toEqual(DEFAULT_SETTINGS);
    expect(validateSettings(42)).toEqual(DEFAULT_SETTINGS);
  });

  it('keeps valid fields', () => {
    const s = validateSettings({ temperature: 1.2, theme: 'dark', model: 'ornith-en' });
    expect(s.temperature).toBe(1.2);
    expect(s.theme).toBe('dark');
  });

  it('clamps out-of-range numbers instead of discarding the file', () => {
    expect(validateSettings({ temperature: 99 }).temperature).toBe(2);
    expect(validateSettings({ temperature: -5 }).temperature).toBe(0);
    expect(validateSettings({ topP: 3 }).topP).toBe(1);
    expect(validateSettings({ numCtx: 10 }).numCtx).toBe(2048);
    expect(validateSettings({ numCtx: 10_000_000 }).numCtx).toBe(131072);
  });

  it('replaces only the invalid field, keeping the valid ones', () => {
    const s = validateSettings({ temperature: 'hot', theme: 'dark' });
    expect(s.temperature).toBe(DEFAULT_SETTINGS.temperature); // reset
    expect(s.theme).toBe('dark'); // kept
  });

  it('rejects a bad theme value', () => {
    expect(validateSettings({ theme: 'neon' }).theme).toBe('system');
  });

  it('rejects a non-http url and strips a trailing slash from a good one', () => {
    expect(validateSettings({ ollamaUrl: 'notaurl' }).ollamaUrl).toBe(DEFAULT_SETTINGS.ollamaUrl);
    expect(validateSettings({ ollamaUrl: 'http://127.0.0.1:1234/' }).ollamaUrl).toBe(
      'http://127.0.0.1:1234',
    );
  });

  it('rejects NaN and Infinity', () => {
    expect(validateSettings({ temperature: NaN }).temperature).toBe(0.6);
    expect(validateSettings({ topP: Infinity }).topP).toBe(0.95);
  });

  it('floors a fractional numCtx', () => {
    expect(validateSettings({ numCtx: 8192.7 }).numCtx).toBe(8192);
  });
});

describe('settings store', () => {
  let dir: string;
  let file: string;

  beforeEach(() => {
    dir = mkdtempSync(path.join(tmpdir(), 'ornith-settings-'));
    file = path.join(dir, 'settings.json');
  });

  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it('uses defaults when no file exists and does not create one', () => {
    const store = createSettingsStore(file);
    expect(store.get()).toEqual(DEFAULT_SETTINGS);
    expect(store.corruptBackupPath).toBeNull();
  });

  it('persists an update and reloads it', () => {
    createSettingsStore(file).update({ temperature: 1.1, theme: 'light' });

    const reloaded = createSettingsStore(file).get();
    expect(reloaded.temperature).toBe(1.1);
    expect(reloaded.theme).toBe('light');
  });

  it('backs up a corrupt file and falls back to defaults', () => {
    writeFileSync(file, '{ this is not json');
    const store = createSettingsStore(file);

    expect(store.get()).toEqual(DEFAULT_SETTINGS);
    expect(store.corruptBackupPath).not.toBeNull();
    expect(readdirSync(dir).some((f) => f.startsWith('settings.corrupt-'))).toBe(true);
  });

  it('leaves no .tmp file behind after an atomic write', () => {
    createSettingsStore(file).update({ temperature: 0.9 });
    expect(readdirSync(dir)).toEqual(['settings.json']);
  });

  it('writes valid JSON that round-trips', () => {
    createSettingsStore(file).update({ numCtx: 16384 });
    const parsed = JSON.parse(readFileSync(file, 'utf8'));
    expect(parsed.numCtx).toBe(16384);
    expect(parsed.schemaVersion).toBe(1);
  });

  it('notifies a change listener', () => {
    const seen: number[] = [];
    const store = createSettingsStore(file, (s) => seen.push(s.temperature));
    store.update({ temperature: 0.3 });
    expect(seen).toEqual([0.3]);
  });

  it('sanitises an invalid patch rather than storing it', () => {
    const store = createSettingsStore(file);
    expect(store.update({ temperature: 99 }).temperature).toBe(2);
  });
});
