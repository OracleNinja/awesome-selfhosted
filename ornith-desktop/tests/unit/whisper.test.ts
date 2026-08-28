import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  cleanTranscript,
  createWhisperEngine,
  detectWhisper,
  listWhisperModels,
  resolveModel,
  whisperArgs,
} from '../../electron/voice/whisper';

const notWindows = process.platform !== 'win32';

describe('whisper model discovery', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-whisper-'));
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('finds .bin models and ignores everything else', () => {
    writeFileSync(path.join(root, 'ggml-base.en.bin'), 'x');
    writeFileSync(path.join(root, 'README.md'), 'x');
    writeFileSync(path.join(root, 'notes.txt'), 'x');

    expect(listWhisperModels(root)).toEqual(['ggml-base.en.bin']);
  });

  it('returns nothing rather than throwing for a directory that is not there', () => {
    expect(listWhisperModels(path.join(root, 'absent'))).toEqual([]);
    expect(resolveModel(path.join(root, 'absent'))).toBeNull();
  });

  it('picks deterministically when a drive carries several models', () => {
    writeFileSync(path.join(root, 'ggml-small.en.bin'), 'x');
    writeFileSync(path.join(root, 'ggml-base.en.bin'), 'x');

    expect(listWhisperModels(root)).toEqual(['ggml-base.en.bin', 'ggml-small.en.bin']);
    expect(resolveModel(root)).toBe(path.join(root, 'ggml-base.en.bin'));
  });
});

describe('detectWhisper', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-whisper-'));
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('is unavailable when the drive carries no binary for this computer', () => {
    const result = detectWhisper({ binaryPath: null, modelDir: root });
    expect(result.available).toBe(false);
    expect(result.engine).toBe('whisper-cpp');
    expect(result.reason).toMatch(/no speech-to-text engine/i);
  });

  it('is unavailable when the binary path points at nothing', () => {
    const result = detectWhisper({ binaryPath: path.join(root, 'absent'), modelDir: root });
    expect(result.available).toBe(false);
  });

  it('names the directory the model should go in, so the reason is actionable', () => {
    const binary = path.join(root, 'whisper-cli');
    writeFileSync(binary, '');
    const modelDir = path.join(root, 'models');

    const result = detectWhisper({ binaryPath: binary, modelDir });
    expect(result.available).toBe(false);
    expect(result.reason).toContain(modelDir);
  });

  it('is available once both the binary and a model are present', () => {
    const binary = path.join(root, 'whisper-cli');
    writeFileSync(binary, '');
    writeFileSync(path.join(root, 'ggml-base.en.bin'), 'x');

    expect(detectWhisper({ binaryPath: binary, modelDir: root }).available).toBe(true);
  });
});

describe('whisperArgs', () => {
  it('writes the transcript to a file rather than leaving it in stdout', () => {
    const args = whisperArgs('/m/model.bin', '/tmp/in.wav', '/tmp/out', 'en-US');

    expect(args).toContain('--output-txt');
    expect(args[args.indexOf('--output-file') + 1]).toBe('/tmp/out');
    expect(args).toContain('--no-timestamps');
  });

  it('passes the bare language code whisper expects, not the BCP-47 tag', () => {
    expect(whisperArgs('m', 'w', 'o', 'en-US')[5]).toBe('en');
    expect(whisperArgs('m', 'w', 'o', 'fr-CA')[5]).toBe('fr');
    expect(whisperArgs('m', 'w', 'o', 'de')[5]).toBe('de');
  });

  it('names the model and the audio file', () => {
    const args = whisperArgs('/m/model.bin', '/tmp/in.wav', '/tmp/out', 'en-US');
    expect(args[args.indexOf('--model') + 1]).toBe('/m/model.bin');
    expect(args[args.indexOf('--file') + 1]).toBe('/tmp/in.wav');
  });
});

describe('cleanTranscript', () => {
  it('drops the annotations whisper emits for non-speech', () => {
    expect(cleanTranscript('[BLANK_AUDIO]')).toBe('');
    expect(cleanTranscript('hello [MUSIC] world')).toBe('hello world');
    expect(cleanTranscript('(silence) hi')).toBe('hi');
  });

  it('keeps ordinary parenthetical speech, which is not an annotation', () => {
    expect(cleanTranscript('the answer (I think) is four')).toBe('the answer (I think) is four');
  });

  it('collapses the whitespace whisper leaves around lines', () => {
    expect(cleanTranscript('  hello\n  world  \n')).toBe('hello world');
  });
});

describe('whisper transcription', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-whisper-'));
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  /** A stand-in for whisper-cli: writes a transcript where the real one would. */
  function fakeWhisper(body: string): string {
    const binary = path.join(root, 'whisper-cli');
    writeFileSync(binary, body);
    chmodSync(binary, 0o755);
    mkdirSync(path.join(root, 'models'), { recursive: true });
    writeFileSync(path.join(root, 'models', 'ggml-base.en.bin'), 'model');
    return binary;
  }

  it('fails with the reason rather than throwing when unavailable', async () => {
    const engine = createWhisperEngine({ binaryPath: null, modelDir: root });
    const result = await engine.transcribe({ wav: new Uint8Array([1]), locale: 'en-US' });

    expect(result.text).toBe('');
    expect(result.error).toMatch(/no speech-to-text engine/i);
  });

  it.skipIf(!notWindows)('reads the transcript the binary wrote', async () => {
    // Mirrors the real contract: --output-file <base> produces <base>.txt.
    const binary = fakeWhisper(
      '#!/bin/sh\n' +
        'while [ $# -gt 0 ]; do\n' +
        '  if [ "$1" = "--output-file" ]; then out="$2"; fi\n' +
        '  shift\n' +
        'done\n' +
        'printf "  hello from the drive [BLANK_AUDIO]\\n" > "$out.txt"\n',
    );

    const engine = createWhisperEngine({
      binaryPath: binary,
      modelDir: path.join(root, 'models'),
    });
    const result = await engine.transcribe({ wav: new Uint8Array([1, 2, 3]), locale: 'en-US' });

    expect(result.error).toBeUndefined();
    expect(result.text).toBe('hello from the drive');
  });

  it.skipIf(!notWindows)('surfaces the binary’s own last words when it fails', async () => {
    const binary = fakeWhisper('#!/bin/sh\necho "error: failed to load model" >&2\nexit 1\n');

    const engine = createWhisperEngine({
      binaryPath: binary,
      modelDir: path.join(root, 'models'),
    });
    const result = await engine.transcribe({ wav: new Uint8Array([1]), locale: 'en-US' });

    expect(result.text).toBe('');
    expect(result.error).toContain('failed to load model');
  });

  it.skipIf(!notWindows)('reports a binary that exits cleanly but writes nothing', async () => {
    const binary = fakeWhisper('#!/bin/sh\nexit 0\n');

    const engine = createWhisperEngine({
      binaryPath: binary,
      modelDir: path.join(root, 'models'),
    });
    const result = await engine.transcribe({ wav: new Uint8Array([1]), locale: 'en-US' });

    expect(result.text).toBe('');
    expect(result.error).toMatch(/no transcript/i);
  });

  it.skipIf(!notWindows)('leaves no audio behind on disk afterwards', async () => {
    const seen = path.join(root, 'seen.txt');
    const binary = fakeWhisper(
      '#!/bin/sh\n' +
        'while [ $# -gt 0 ]; do\n' +
        '  if [ "$1" = "--file" ]; then wav="$2"; fi\n' +
        '  if [ "$1" = "--output-file" ]; then out="$2"; fi\n' +
        '  shift\n' +
        'done\n' +
        `printf "%s" "$wav" > "${seen}"\n` +
        'printf "x" > "$out.txt"\n',
    );

    const engine = createWhisperEngine({
      binaryPath: binary,
      modelDir: path.join(root, 'models'),
    });
    await engine.transcribe({ wav: new Uint8Array([1]), locale: 'en-US' });

    const wavPath = readFileSync(seen, 'utf8').trim();
    expect(wavPath).toMatch(/input\.wav$/);
    expect(existsSync(wavPath)).toBe(false);
  });
});
