import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { chmodSync, mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createSttEngine, detectStt, findHelper, helperCandidates } from '../../electron/voice/stt';
import { detectTts } from '../../electron/voice/tts';

describe('STT engine resolution', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-stt-test-'));
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('is unavailable off macOS, with a reason the UI can show', () => {
    const result = detectStt({ appRoot: root, resourcesPath: root, platform: 'linux' });
    expect(result.available).toBe(false);
    expect(result.engine).toBe('macos-speech');
    expect(result.reason).toMatch(/macOS/i);
  });

  it('is unavailable on macOS when the helper has not been built', () => {
    const result = detectStt({ appRoot: root, resourcesPath: root, platform: 'darwin' });
    expect(result.available).toBe(false);
    expect(result.reason).toMatch(/build:stt/);
  });

  it('becomes available once the helper exists', () => {
    mkdirSync(path.join(root, 'native', 'build'), { recursive: true });
    const helper = path.join(root, 'native', 'build', 'ornith-stt');
    writeFileSync(helper, '#!/bin/sh\necho "{}"\n');
    chmodSync(helper, 0o755);

    const result = detectStt({ appRoot: root, resourcesPath: root, platform: 'darwin' });
    expect(result.available).toBe(true);
  });

  it('prefers the packaged resources path over the source build', () => {
    const candidates = helperCandidates('/app', '/resources');
    expect(candidates[0]).toBe(path.join('/resources', 'ornith-stt'));
    expect(candidates[1]).toBe(path.join('/app', 'native', 'build', 'ornith-stt'));
  });

  it('findHelper returns null when nothing exists', () => {
    expect(findHelper([path.join(root, 'nope')])).toBeNull();
  });

  it('transcribe fails gracefully rather than throwing when unavailable', async () => {
    const engine = createSttEngine({ appRoot: root, resourcesPath: root, platform: 'linux' });
    const result = await engine.transcribe({ wav: new Uint8Array([1, 2, 3]), locale: 'en-US' });
    expect(result.text).toBe('');
    expect(result.error).toMatch(/macOS/i);
  });

  // The helper contract: a JSON object on stdout. Exercised with a shell stub so
  // the parsing path is covered without needing Xcode.
  it('parses a transcript from the helper', async () => {
    mkdirSync(path.join(root, 'native', 'build'), { recursive: true });
    const helper = path.join(root, 'native', 'build', 'ornith-stt');
    writeFileSync(helper, '#!/bin/sh\necho \'{"text":"hello world"}\'\n');
    chmodSync(helper, 0o755);

    const engine = createSttEngine({ appRoot: root, resourcesPath: root, platform: 'darwin' });
    const result = await engine.transcribe({ wav: new Uint8Array([1, 2, 3]), locale: 'en-US' });
    expect(result.text).toBe('hello world');
    expect(result.error).toBeUndefined();
  });

  it('surfaces an error reported by the helper', async () => {
    mkdirSync(path.join(root, 'native', 'build'), { recursive: true });
    const helper = path.join(root, 'native', 'build', 'ornith-stt');
    writeFileSync(helper, '#!/bin/sh\necho \'{"error":"permission denied"}\'\n');
    chmodSync(helper, 0o755);

    const engine = createSttEngine({ appRoot: root, resourcesPath: root, platform: 'darwin' });
    const result = await engine.transcribe({ wav: new Uint8Array([1]), locale: 'en-US' });
    expect(result.text).toBe('');
    expect(result.error).toBe('permission denied');
  });

  it('does not leave the temporary audio file behind', async () => {
    mkdirSync(path.join(root, 'native', 'build'), { recursive: true });
    const helper = path.join(root, 'native', 'build', 'ornith-stt');
    // Record the path it was given so the test can assert cleanup.
    writeFileSync(helper, '#!/bin/sh\necho "$1" > "' + root + '/seen.txt"\necho \'{"text":"x"}\'\n');
    chmodSync(helper, 0o755);

    const engine = createSttEngine({ appRoot: root, resourcesPath: root, platform: 'darwin' });
    await engine.transcribe({ wav: new Uint8Array([1]), locale: 'en-US' });

    const seen = (await import('node:fs')).readFileSync(path.join(root, 'seen.txt'), 'utf8').trim();
    expect(seen).toMatch(/input\.wav$/);
    expect((await import('node:fs')).existsSync(seen)).toBe(false);
  });
});

describe('TTS engine detection', () => {
  it('reports unavailable off macOS with an actionable reason', () => {
    // This suite runs on Linux in CI, which is exactly the unsupported case.
    const result = detectTts();
    if (process.platform === 'darwin') {
      expect(result.available).toBe(true);
      expect(result.engine).toBe('macos-say');
    } else {
      expect(result.available).toBe(false);
      expect(result.reason).toMatch(/macOS/i);
    }
  });
});
