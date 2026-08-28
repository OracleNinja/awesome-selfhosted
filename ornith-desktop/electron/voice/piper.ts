/**
 * Text-to-speech via a drive-bundled Piper binary (https://github.com/rhasspy/piper).
 *
 * macOS has `say`; Windows and Linux have nothing dependable to shell out to,
 * so the portable drive carries Piper and its voices instead. Piper does not
 * touch the speakers — it writes a WAV — which is why this module's whole job
 * is to return bytes. The renderer plays them (TtsPlayback `renderer`), and
 * that split is what keeps the strict CSP and the local-only promise intact.
 *
 * Structured like the STT engine: pure helpers that tests can exercise without
 * a binary present, plus a factory returning the interface the IPC layer uses.
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { DEFAULT_SETTINGS } from '../../shared/defaults';
import type { EngineAvailability, SynthesisResult } from '../../shared/voice';
import { log } from '../log';

/** A voice is a model file plus the config Piper needs to interpret it. */
const VOICE_EXT = '.onnx';
const CONFIG_EXT = '.onnx.json';

/**
 * Piper's `--length_scale` is a duration multiplier: above 1 is slower, below
 * is faster. The models are tuned to sound natural at scale 1, which is the
 * app's default words-per-minute, so that rate is the pivot.
 */
const REFERENCE_RATE = DEFAULT_SETTINGS.speechRate;

/**
 * Beyond these the voice stops being intelligible, and SETTINGS_BOUNDS alone
 * does not bound the scale — a rate is only advisory once a drive's settings
 * file has been hand-edited.
 */
const MIN_LENGTH_SCALE = 0.4;
const MAX_LENGTH_SCALE = 2.5;

/** A long reply on a slow USB drive is still far inside this. */
const SYNTHESIS_TIMEOUT_MS = 60_000;

/** Piper narrates its progress on stderr, so only the tail is worth keeping. */
const MAX_STDERR_CHARS = 2000;

export interface PiperOptions {
  /** Absolute path to the piper binary, or null when the drive carries none. */
  binaryPath: string | null;
  /** Directory holding `.onnx` voices, each beside its `.onnx.json` config. */
  voiceDir: string;
}

export interface PiperEngine {
  availability(): EngineAvailability;
  listVoices(): string[];
  synthesize(request: { text: string; voice: string; rate: number }): Promise<SynthesisResult>;
}

export function detectPiper(options: PiperOptions): EngineAvailability {
  const engine = 'piper';

  if (!options.binaryPath) {
    return {
      available: false,
      engine,
      reason:
        'This drive does not carry a Piper speech binary. Add `piper` to the runtime folder on this drive to hear spoken replies.',
    };
  }

  if (!existsSync(options.binaryPath)) {
    return {
      available: false,
      engine,
      reason: `The Piper speech binary is missing from ${options.binaryPath}.`,
    };
  }

  if (listPiperVoices(options.voiceDir).length === 0) {
    return {
      available: false,
      engine,
      reason: `No Piper voices were found. Put a voice — an ${VOICE_EXT} file next to its ${CONFIG_EXT} config — in ${options.voiceDir}.`,
    };
  }

  return { available: true, engine };
}

/**
 * Voice names (no extension) that are actually usable. A model whose config is
 * missing cannot be loaded, so offering it in the settings dialog would only
 * produce a failure later; it is filtered out here instead.
 */
export function listPiperVoices(voiceDir: string): string[] {
  let names: Set<string>;
  try {
    names = new Set(
      readdirSync(voiceDir, { withFileTypes: true })
        .filter((entry) => entry.isFile())
        .map((entry) => entry.name),
    );
  } catch {
    // The drive may be absent, unreadable, or not yet provisioned. None of
    // those is an error worth throwing at the UI — there are simply no voices.
    return [];
  }

  return [...names]
    .filter((name) => name.endsWith(VOICE_EXT) && names.has(`${name}.json`))
    .map((name) => name.slice(0, -VOICE_EXT.length))
    .sort();
}

/**
 * The voice name comes from a settings file on a removable drive, so it is
 * attacker-controlled for our purposes: anything that could steer the read
 * outside `voiceDir` is refused rather than sanitised.
 */
function isSafeVoiceName(name: string): boolean {
  if (name.includes('/') || name.includes('\\')) return false;
  if (name.includes('..')) return false;
  if (/^[a-zA-Z]:/.test(name)) return false; // drive-relative path, e.g. C:voice
  return !name.includes('\0');
}

export function resolveVoicePath(voiceDir: string, voice: string): string | null {
  const voices = listPiperVoices(voiceDir);
  const requested = voice.trim();

  if (!requested) {
    // Blank means "whichever voice this drive has"; listing order is sorted,
    // so the same drive always defaults to the same voice.
    const first = voices[0];
    return first === undefined ? null : path.join(voiceDir, `${first}${VOICE_EXT}`);
  }

  if (!isSafeVoiceName(requested)) {
    log.warn('piper.voice.rejected', { voice: requested });
    return null;
  }

  const name = requested.endsWith(VOICE_EXT) ? requested.slice(0, -VOICE_EXT.length) : requested;
  if (!voices.includes(name)) return null;

  return path.join(voiceDir, `${name}${VOICE_EXT}`);
}

/** Words per minute → Piper's duration multiplier. See REFERENCE_RATE. */
export function lengthScaleFor(rate: number): number {
  if (!Number.isFinite(rate) || rate <= 0) return 1;
  const scale = REFERENCE_RATE / rate;
  return Math.min(MAX_LENGTH_SCALE, Math.max(MIN_LENGTH_SCALE, scale));
}

export function piperArgs(modelPath: string, outputPath: string, rate: number): string[] {
  return [
    '--model',
    modelPath,
    '--output_file',
    outputPath,
    '--length_scale',
    String(lengthScaleFor(rate)),
  ];
}

function lastNonEmptyLine(text: string): string {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  return lines.at(-1) ?? '';
}

/** Resolves when Piper exits cleanly; rejects with a one-line explanation. */
function runPiper(binary: string, args: string[], text: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, args, { stdio: ['pipe', 'ignore', 'pipe'] });

    let stderr = '';
    let settled = false;

    const timer = setTimeout(() => {
      // SIGKILL rather than SIGTERM: a wedged synthesis is not going to shut
      // down politely, and the drive must not be pinned by a zombie child.
      child.kill('SIGKILL');
      finish(new Error(`piper did not finish within ${SYNTHESIS_TIMEOUT_MS / 1000} seconds.`));
    }, SYNTHESIS_TIMEOUT_MS);

    // A declaration, not a const arrow: the timeout above needs it, and it
    // needs the timeout. Only one of the two can be hoisted.
    function finish(err: Error | null): void {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (err) reject(err);
      else resolve();
    }

    child.stderr?.on('data', (chunk: Buffer) => {
      stderr = (stderr + chunk.toString()).slice(-MAX_STDERR_CHARS);
    });

    child.on('error', (err) => {
      finish(new Error(`could not start ${path.basename(binary)} (${err.message}).`));
    });

    child.on('close', (code) => {
      if (code === 0) {
        finish(null);
        return;
      }
      const detail = lastNonEmptyLine(stderr);
      finish(new Error(detail ? `piper exited with code ${code}: ${detail}` : `piper exited with code ${code}.`));
    });

    // Text goes over stdin, never argv: a reply can exceed the argv limit, and
    // stdin sidesteps every quoting question on every platform.
    child.stdin?.on('error', () => {
      /* the child can exit before the write drains */
    });
    child.stdin?.write(text);
    child.stdin?.end();
  });
}

function readSynthesised(outputPath: string): Uint8Array {
  if (!existsSync(outputPath)) throw new Error('the voice produced no audio file.');

  const bytes = readFileSync(outputPath);
  if (bytes.length === 0) throw new Error('the voice produced an empty audio file.');

  // Copy out of the Buffer pool: these bytes are structured-cloned to the
  // renderer, and a pooled Buffer would carry its neighbours' memory along.
  return new Uint8Array(bytes);
}

export function createPiperEngine(options: PiperOptions): PiperEngine {
  return {
    availability: () => detectPiper(options),

    listVoices: () => listPiperVoices(options.voiceDir),

    async synthesize(request): Promise<SynthesisResult> {
      const detection = detectPiper(options);
      if (!detection.available) {
        return { wav: null, error: detection.reason ?? 'Piper speech is unavailable.' };
      }

      // detectPiper already proved this, but narrowing it here beats a
      // non-null assertion.
      const binary = options.binaryPath;
      if (!binary) return { wav: null, error: 'This drive does not carry a Piper speech binary.' };

      const text = request.text.trim();
      if (!text) return { wav: null, error: 'There was nothing to speak.' };

      const modelPath = resolveVoicePath(options.voiceDir, request.voice);
      if (!modelPath) {
        return {
          wav: null,
          error: `The voice "${request.voice}" was not found in ${options.voiceDir}.`,
        };
      }

      // A fresh directory per request: two replies can be in flight, and the
      // audio should exist on disk only as long as the call takes.
      const dir = mkdtempSync(path.join(tmpdir(), 'ornith-piper-'));
      const outputPath = path.join(dir, 'speech.wav');

      try {
        await runPiper(binary, piperArgs(modelPath, outputPath, request.rate), text);
        return { wav: readSynthesised(outputPath) };
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        log.warn('piper.synthesis.failed', { detail });
        return { wav: null, error: `Speech synthesis failed: ${detail}` };
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    },
  };
}
