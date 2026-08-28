import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  createPiperEngine,
  detectPiper,
  lengthScaleFor,
  listPiperVoices,
  piperArgs,
  resolveVoicePath,
} from '../../electron/voice/piper';

/** A voice is only usable when its config sits beside the model. */
function addVoice(dir: string, name: string, withConfig = true): void {
  writeFileSync(path.join(dir, `${name}.onnx`), 'model bytes');
  if (withConfig) writeFileSync(path.join(dir, `${name}.onnx.json`), '{"sample_rate":22050}');
}

/** A 44-byte RIFF header plus four frames — enough to be a real WAV. */
function wavBytes(): Uint8Array {
  const samples = Buffer.from([0x00, 0x00, 0x10, 0x00, 0xf0, 0xff, 0x00, 0x00]);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0, 'ascii');
  header.writeUInt32LE(36 + samples.length, 4);
  header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii');
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(1, 22); // mono
  header.writeUInt32LE(22050, 24);
  header.writeUInt32LE(44100, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36, 'ascii');
  header.writeUInt32LE(samples.length, 40);
  return new Uint8Array(Buffer.concat([header, samples]));
}

function writeScript(file: string, body: string): string {
  writeFileSync(file, body);
  chmodSync(file, 0o755);
  return file;
}

describe('Piper detection', () => {
  let root: string;
  let voiceDir: string;
  let binary: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-piper-test-'));
    voiceDir = path.join(root, 'voice');
    mkdirSync(voiceDir, { recursive: true });
    binary = path.join(root, 'piper');
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('reports the three ways a drive can lack Piper with three different reasons', () => {
    const noBinary = detectPiper({ binaryPath: null, voiceDir });

    const missingBinary = detectPiper({ binaryPath: binary, voiceDir });

    writeScript(binary, '#!/bin/sh\nexit 0\n');
    const noVoice = detectPiper({ binaryPath: binary, voiceDir });

    for (const result of [noBinary, missingBinary, noVoice]) {
      expect(result.available).toBe(false);
      expect(result.engine).toBe('piper');
      expect(result.reason).toBeTruthy();
    }

    // Each case must tell the user something different, or the message is
    // useless for working out what to put on the drive.
    const reasons = new Set([noBinary.reason, missingBinary.reason, noVoice.reason]);
    expect(reasons.size).toBe(3);

    expect(noBinary.reason).toMatch(/piper/i);
    expect(missingBinary.reason).toContain(binary);
    expect(noVoice.reason).toContain(voiceDir);
    expect(noVoice.reason).toMatch(/\.onnx/);
  });

  it('is available once the binary and a complete voice are both present', () => {
    writeScript(binary, '#!/bin/sh\nexit 0\n');
    addVoice(voiceDir, 'en_US-amy-medium');

    const result = detectPiper({ binaryPath: binary, voiceDir });
    expect(result).toEqual({ available: true, engine: 'piper' });
  });

  it('a voice missing its config does not make the engine available', () => {
    writeScript(binary, '#!/bin/sh\nexit 0\n');
    addVoice(voiceDir, 'orphan', false);

    expect(detectPiper({ binaryPath: binary, voiceDir }).available).toBe(false);
  });
});

describe('Piper voice listing', () => {
  let voiceDir: string;

  beforeEach(() => {
    voiceDir = mkdtempSync(path.join(tmpdir(), 'ornith-piper-voices-'));
  });

  afterEach(() => rmSync(voiceDir, { recursive: true, force: true }));

  it('lists complete voices, sorted, without the .onnx extension', () => {
    addVoice(voiceDir, 'zeta');
    addVoice(voiceDir, 'alpha');
    addVoice(voiceDir, 'mid');

    expect(listPiperVoices(voiceDir)).toEqual(['alpha', 'mid', 'zeta']);
  });

  it('skips a model whose .onnx.json config is missing', () => {
    addVoice(voiceDir, 'complete');
    addVoice(voiceDir, 'orphan', false);

    expect(listPiperVoices(voiceDir)).toEqual(['complete']);
  });

  it('ignores unrelated files in the voice folder', () => {
    addVoice(voiceDir, 'amy');
    writeFileSync(path.join(voiceDir, 'README.txt'), 'notes');
    writeFileSync(path.join(voiceDir, 'stray.onnx.json'), '{}');

    expect(listPiperVoices(voiceDir)).toEqual(['amy']);
  });

  it('returns nothing rather than throwing when the directory is absent', () => {
    // The drive can be unplugged mid-session; that must not raise.
    expect(listPiperVoices(path.join(voiceDir, 'nope'))).toEqual([]);
  });
});

describe('Piper voice resolution', () => {
  let voiceDir: string;

  beforeEach(() => {
    voiceDir = mkdtempSync(path.join(tmpdir(), 'ornith-piper-resolve-'));
    addVoice(voiceDir, 'en_GB-alan');
    addVoice(voiceDir, 'en_US-amy');
  });

  afterEach(() => rmSync(voiceDir, { recursive: true, force: true }));

  it('accepts the name with or without the .onnx suffix', () => {
    const expected = path.join(voiceDir, 'en_US-amy.onnx');
    expect(resolveVoicePath(voiceDir, 'en_US-amy')).toBe(expected);
    expect(resolveVoicePath(voiceDir, 'en_US-amy.onnx')).toBe(expected);
  });

  it('treats a blank name as the default, which is the first listed voice', () => {
    const first = path.join(voiceDir, 'en_GB-alan.onnx');
    expect(resolveVoicePath(voiceDir, '')).toBe(first);
    expect(resolveVoicePath(voiceDir, '   ')).toBe(first);
  });

  it('has no default when the drive carries no voices', () => {
    const empty = mkdtempSync(path.join(tmpdir(), 'ornith-piper-empty-'));
    try {
      expect(resolveVoicePath(empty, '')).toBeNull();
    } finally {
      rmSync(empty, { recursive: true, force: true });
    }
  });

  it('returns null for a voice the drive does not carry', () => {
    expect(resolveVoicePath(voiceDir, 'de_DE-thorsten')).toBeNull();
  });

  it('refuses names that would read outside the voice directory', () => {
    // Settings come off a removable drive, so the name is untrusted input.
    for (const hostile of ['../x', 'a/b', 'a\\b', 'C:\\x', '../en_US-amy', 'C:en_US-amy']) {
      expect(resolveVoicePath(voiceDir, hostile)).toBeNull();
    }
  });
});

describe('Piper rate mapping', () => {
  it('maps the default rate to an unchanged duration', () => {
    expect(lengthScaleFor(175)).toBe(1);
  });

  it('a faster rate shortens durations and a slower one lengthens them', () => {
    expect(lengthScaleFor(250)).toBeLessThan(1);
    expect(lengthScaleFor(120)).toBeGreaterThan(1);
    // The whole settings range stays inside the clamp.
    expect(lengthScaleFor(400)).toBeGreaterThan(0.4);
    expect(lengthScaleFor(80)).toBeLessThan(2.5);
  });

  it('clamps at both ends so an edited settings file cannot produce noise', () => {
    expect(lengthScaleFor(100_000)).toBe(0.4);
    expect(lengthScaleFor(1)).toBe(2.5);
  });

  it('falls back to unchanged duration for a nonsensical rate', () => {
    expect(lengthScaleFor(0)).toBe(1);
    expect(lengthScaleFor(Number.NaN)).toBe(1);
    expect(lengthScaleFor(-175)).toBe(1);
    expect(lengthScaleFor(Number.POSITIVE_INFINITY)).toBe(1);
  });
});

describe('Piper argument construction', () => {
  it('passes the model, the output path and the length scale, in that order', () => {
    expect(piperArgs('/voices/amy.onnx', '/tmp/out.wav', 175)).toEqual([
      '--model',
      '/voices/amy.onnx',
      '--output_file',
      '/tmp/out.wav',
      '--length_scale',
      '1',
    ]);
  });

  it('stringifies the scale it computed for the rate', () => {
    const args = piperArgs('/voices/amy.onnx', '/tmp/out.wav', 350);
    expect(args[5]).toBe(String(lengthScaleFor(350)));
    expect(args).toHaveLength(6);
  });
});

describe('Piper synthesis', () => {
  let root: string;
  let voiceDir: string;
  let binary: string;
  let reference: string;
  let stdinLog: string;
  let outputLog: string;

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'ornith-piper-synth-'));
    voiceDir = path.join(root, 'voice');
    mkdirSync(voiceDir, { recursive: true });
    addVoice(voiceDir, 'en_US-amy');

    binary = path.join(root, 'piper');
    reference = path.join(root, 'reference.wav');
    stdinLog = path.join(root, 'stdin.txt');
    outputLog = path.join(root, 'output-path.txt');
    writeFileSync(reference, wavBytes());
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  /** Stands in for the real binary: reads stdin, writes a WAV where told. */
  function fakePiper(): void {
    writeScript(
      binary,
      [
        '#!/bin/sh',
        `cat > "${stdinLog}"`,
        'out=""',
        'while [ "$#" -gt 0 ]; do',
        '  if [ "$1" = "--output_file" ]; then out="$2"; fi',
        '  shift',
        'done',
        `printf '%s' "$out" > "${outputLog}"`,
        'echo "processed 1 sentence" >&2',
        `cp "${reference}" "$out"`,
      ].join('\n') + '\n',
    );
  }

  it('reports the unavailable reason instead of throwing', async () => {
    const engine = createPiperEngine({ binaryPath: null, voiceDir });
    const result = await engine.synthesize({ text: 'hello', voice: '', rate: 175 });
    expect(result.wav).toBeNull();
    expect(result.error).toMatch(/piper/i);
  });

  it('refuses blank text', async () => {
    writeScript(binary, '#!/bin/sh\nexit 0\n');
    const engine = createPiperEngine({ binaryPath: binary, voiceDir });

    const result = await engine.synthesize({ text: '   \n ', voice: '', rate: 175 });
    expect(result.wav).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('reports a voice it cannot resolve, naming the folder to fix', async () => {
    writeScript(binary, '#!/bin/sh\nexit 0\n');
    const engine = createPiperEngine({ binaryPath: binary, voiceDir });

    const result = await engine.synthesize({ text: 'hello', voice: '../escape', rate: 175 });
    expect(result.wav).toBeNull();
    expect(result.error).toContain(voiceDir);
  });

  it('exposes the same availability and voice list as the free functions', () => {
    writeScript(binary, '#!/bin/sh\nexit 0\n');
    const engine = createPiperEngine({ binaryPath: binary, voiceDir });

    expect(engine.availability()).toEqual(detectPiper({ binaryPath: binary, voiceDir }));
    expect(engine.listVoices()).toEqual(['en_US-amy']);
  });

  // Spawning a `#!/bin/sh` stub is a POSIX-only trick; the logic it covers is
  // platform-independent, so skipping on Windows loses nothing.
  it.skipIf(process.platform === 'win32')(
    'returns the WAV the binary wrote and cleans up after itself',
    async () => {
      fakePiper();
      const engine = createPiperEngine({ binaryPath: binary, voiceDir });

      const result = await engine.synthesize({
        text: '  Hello there.  ',
        voice: 'en_US-amy',
        rate: 175,
      });

      expect(result.error).toBeUndefined();
      expect(result.wav).toBeInstanceOf(Uint8Array);
      expect(result.wav).toEqual(new Uint8Array(readFileSync(reference)));

      // The text reached the binary on stdin, trimmed, and never as an argument.
      expect(readFileSync(stdinLog, 'utf8')).toBe('Hello there.');

      const outputPath = readFileSync(outputLog, 'utf8').trim();
      expect(path.basename(path.dirname(outputPath))).toMatch(/^ornith-piper-/);
      expect(existsSync(outputPath)).toBe(false);
      expect(existsSync(path.dirname(outputPath))).toBe(false);
    },
  );

  it.skipIf(process.platform === 'win32')(
    'surfaces the last stderr line when the binary fails',
    async () => {
      writeScript(
        binary,
        [
          '#!/bin/sh',
          'cat > /dev/null',
          'echo "Loading model" >&2',
          'echo "espeak-ng data path not found" >&2',
          'echo "" >&2',
          'exit 3',
        ].join('\n') + '\n',
      );

      const engine = createPiperEngine({ binaryPath: binary, voiceDir });
      const result = await engine.synthesize({ text: 'hello', voice: 'en_US-amy', rate: 175 });

      expect(result.wav).toBeNull();
      expect(result.error).toContain('espeak-ng data path not found');
      expect(result.error).toContain('3');
    },
  );

  it.skipIf(process.platform === 'win32')(
    'treats a binary that writes no audio as a failure',
    async () => {
      writeScript(binary, '#!/bin/sh\ncat > /dev/null\nexit 0\n');

      const engine = createPiperEngine({ binaryPath: binary, voiceDir });
      const result = await engine.synthesize({ text: 'hello', voice: 'en_US-amy', rate: 175 });

      expect(result.wav).toBeNull();
      expect(result.error).toMatch(/audio/i);
    },
  );
});
