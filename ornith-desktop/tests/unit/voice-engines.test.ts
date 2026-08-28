import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { chmodSync, mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  createSttEngine,
  detectAppleSpeech,
  detectStt,
  findHelper,
  helperCandidates,
  whisperOptionsFor,
} from '../../electron/voice/stt';
import { resolveLayout, toolBinaryPath } from '../../shared/portable';
import { createTtsEngine, detectSay, detectTts, piperOptionsFor } from '../../electron/voice/tts';

describe('STT engine resolution', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-stt-test-'));
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('the Apple engine alone is unavailable off macOS, with a reason the UI can show', () => {
    const result = detectAppleSpeech({ appRoot: root, resourcesPath: root, platform: 'linux' });
    expect(result.available).toBe(false);
    expect(result.engine).toBe('macos-speech');
    expect(result.reason).toMatch(/macOS/i);
  });

  it('off macOS with no drive, points at the missing whisper build rather than at macOS', () => {
    // "requires macOS" would be a lie now: whisper runs here, it is just absent.
    const result = detectStt({ appRoot: root, resourcesPath: root, platform: 'linux' });
    expect(result.available).toBe(false);
    expect(result.engine).toBe('whisper-cpp');
    expect(result.reason).not.toMatch(/requires macOS/i);
    expect(result.reason).toMatch(/whisper/i);
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
    expect(result.error).toMatch(/speech-to-text engine/i);
  });

  it('an installed app has nowhere to have put whisper, so it reports none', () => {
    const options = whisperOptionsFor({ appRoot: root, resourcesPath: root, platform: 'linux' });
    expect(options.binaryPath).toBeNull();
  });

  it('a drive supplies the whisper paths, per platform slot', () => {
    const layout = resolveLayout('/mnt/stick');
    const options = whisperOptionsFor({
      appRoot: root,
      resourcesPath: root,
      platform: 'win32',
      arch: 'x64',
      layout,
    });

    expect(options.binaryPath).toBe(toolBinaryPath(layout, 'win32', 'x64', 'whisper'));
    expect(options.binaryPath).toMatch(/whisper-cli\.exe$/);
    expect(options.modelDir).toBe(layout.sttModelDir);
  });

  it('on a Mac whose helper was never compiled, a drive-carried whisper still wins', () => {
    const layout = resolveLayout(root);
    const binary = toolBinaryPath(layout, 'darwin', 'arm64', 'whisper');
    mkdirSync(path.dirname(binary), { recursive: true });
    writeFileSync(binary, '#!/bin/sh\nexit 0\n');
    chmodSync(binary, 0o755);
    mkdirSync(layout.sttModelDir, { recursive: true });
    writeFileSync(path.join(layout.sttModelDir, 'ggml-base.en.bin'), 'model');

    const result = detectStt({
      appRoot: path.join(root, 'nowhere'),
      resourcesPath: path.join(root, 'nowhere'),
      platform: 'darwin',
      arch: 'arm64',
      layout,
    });

    expect(result.available).toBe(true);
    expect(result.engine).toBe('whisper-cpp');
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
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-tts-test-'));
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('uses the system voice where there is one, and plays it itself', () => {
    const result = detectTts({ platform: 'darwin' });
    if (process.platform === 'darwin') {
      expect(result.available).toBe(true);
      expect(result.engine).toBe('macos-say');
      expect(result.playback).toBe('native');
    } else {
      // /usr/bin/say does not exist here, so the branch is only reachable in
      // the sense that the platform check passed.
      expect(result.engine).toBe('macos-say');
    }
  });

  it('the system engine alone is macOS-only, and says so', () => {
    const result = detectSay('linux');
    expect(result.available).toBe(false);
    expect(result.reason).toMatch(/macOS/i);
  });

  it('off macOS with no drive, points at the missing Piper build rather than at macOS', () => {
    const result = detectTts({ platform: 'linux' });
    expect(result.available).toBe(false);
    expect(result.engine).toBe('piper');
    expect(result.reason).not.toMatch(/requires macOS/i);
    expect(result.reason).toMatch(/piper/i);
  });

  it('an installed app has nowhere to have put Piper, so it reports none', () => {
    expect(piperOptionsFor({ platform: 'linux' }).binaryPath).toBeNull();
  });

  it('a drive supplies the Piper paths, per platform slot', () => {
    const layout = resolveLayout('/mnt/stick');
    const options = piperOptionsFor({ platform: 'win32', arch: 'x64', layout });

    expect(options.binaryPath).toBe(toolBinaryPath(layout, 'win32', 'x64', 'piper'));
    expect(options.binaryPath).toMatch(/piper\.exe$/);
    expect(options.voiceDir).toBe(layout.ttsVoiceDir);
  });

  it('a drive carrying Piper and a voice speaks on Windows, played by the renderer', () => {
    const layout = resolveLayout(root);
    const binary = toolBinaryPath(layout, 'linux', 'x64', 'piper');
    mkdirSync(path.dirname(binary), { recursive: true });
    writeFileSync(binary, '#!/bin/sh\nexit 0\n');
    chmodSync(binary, 0o755);

    mkdirSync(layout.ttsVoiceDir, { recursive: true });
    writeFileSync(path.join(layout.ttsVoiceDir, 'en_US-lessac-medium.onnx'), 'model');
    writeFileSync(path.join(layout.ttsVoiceDir, 'en_US-lessac-medium.onnx.json'), '{}');

    const result = detectTts({ platform: 'linux', arch: 'x64', layout });
    expect(result.available).toBe(true);
    expect(result.engine).toBe('piper');
    // The drive's engine writes a file; the speakers belong to the renderer.
    expect(result.playback).toBe('renderer');
  });

  /** A drive carrying a working Piper, so the dispatcher has something to pick. */
  function driveWithPiper(body: string): ReturnType<typeof resolveLayout> {
    const layout = resolveLayout(root);
    const binary = toolBinaryPath(layout, process.platform, process.arch, 'piper');
    mkdirSync(path.dirname(binary), { recursive: true });
    writeFileSync(binary, body);
    chmodSync(binary, 0o755);

    mkdirSync(layout.ttsVoiceDir, { recursive: true });
    writeFileSync(path.join(layout.ttsVoiceDir, 'en_US-test-medium.onnx'), 'voice');
    writeFileSync(path.join(layout.ttsVoiceDir, 'en_US-test-medium.onnx.json'), '{}');
    return layout;
  }

  it('lists the drive’s voices when the drive is the engine', async () => {
    const layout = driveWithPiper('#!/bin/sh\nexit 0\n');
    const engine = createTtsEngine({ layout, platform: process.platform, arch: process.arch });

    expect(await engine.listVoices()).toEqual(['en_US-test-medium']);
  });

  it('returns no voices when there is no engine at all', async () => {
    const engine = createTtsEngine({ platform: 'linux' });
    expect(await engine.listVoices()).toEqual([]);
  });

  it.skipIf(process.platform === 'win32')('synthesises through the drive’s engine', async () => {
    const layout = driveWithPiper(
      '#!/bin/sh\n' +
        'while [ $# -gt 0 ]; do\n' +
        '  if [ "$1" = "--output_file" ]; then out="$2"; fi\n' +
        '  shift\n' +
        'done\n' +
        'cat > /dev/null\n' +
        'printf "RIFFfake-wav-bytes" > "$out"\n',
    );

    const engine = createTtsEngine({ layout, platform: process.platform, arch: process.arch });
    const result = await engine.synthesize({
      requestId: 'r1',
      text: 'hello',
      voice: '',
      rate: 175,
    });

    expect(result.error).toBeUndefined();
    expect(Buffer.from(result.wav ?? new Uint8Array()).toString()).toBe('RIFFfake-wav-bytes');
    // The indicator must not be left latched on after synthesis returns.
    expect(engine.speakingRequestId).toBeNull();
  });

  it('refuses to synthesise when no engine can run, rather than throwing', async () => {
    const engine = createTtsEngine({ platform: 'linux' });
    const result = await engine.synthesize({ requestId: 'r1', text: 'hi', voice: '', rate: 175 });

    expect(result.wav).toBeNull();
    expect(result.error).toMatch(/piper/i);
  });

  it('speak() is a no-op on a renderer-playback engine, and still reports done', async () => {
    const layout = driveWithPiper('#!/bin/sh\nexit 0\n');
    const engine = createTtsEngine({ layout, platform: process.platform, arch: process.arch });

    // main calls synthesize() for this engine; speak() must not silently hang
    // if it is ever called by mistake.
    const finished = await new Promise<string>((resolve) => {
      engine.speak({ requestId: 'r2', text: 'hello', voice: '', rate: 175 }, resolve);
    });

    expect(finished).toBe('r2');
  });

  it('stop() is safe when nothing is speaking', () => {
    const engine = createTtsEngine({ platform: 'linux' });
    expect(() => engine.stop()).not.toThrow();
    expect(engine.speakingRequestId).toBeNull();
  });

  it('a voice without its config is not offered, because it cannot be loaded', () => {
    const layout = resolveLayout(root);
    const binary = toolBinaryPath(layout, 'linux', 'x64', 'piper');
    mkdirSync(path.dirname(binary), { recursive: true });
    writeFileSync(binary, '');
    mkdirSync(layout.ttsVoiceDir, { recursive: true });
    writeFileSync(path.join(layout.ttsVoiceDir, 'orphan.onnx'), 'model');

    const result = detectTts({ platform: 'linux', arch: 'x64', layout });
    expect(result.available).toBe(false);
    expect(result.reason).toContain(layout.ttsVoiceDir);
  });
});
