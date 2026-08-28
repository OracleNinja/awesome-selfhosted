/**
 * Speech-to-text, dispatched across two on-device engines.
 *
 * Deliberately NOT the Web Speech API: Chromium's webkitSpeechRecognition
 * streams microphone audio to Google's servers, which would violate the
 * local-only requirement while looking like a browser API.
 *
 * Two concrete engines sit behind one interface:
 *
 *   `macos-speech` — the system Speech framework via a small Swift helper,
 *   with `requiresOnDeviceRecognition`. Preferred where it exists: already on
 *   the machine, no model to carry, and faster than whisper on the same audio.
 *
 *   `whisper-cpp` — a binary and a model the drive carries. This is what makes
 *   dictation work on a Windows or Linux machine the drive is plugged into,
 *   which the Apple path can never do.
 *
 * Preference order is availability, not platform: whichever can actually run
 * wins, so a drive carrying whisper still dictates on a Mac whose helper was
 * never compiled.
 */
import { execFile } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import type { EngineAvailability, TranscriptionRequest, TranscriptionResult } from '../../shared/voice';
import { toolBinaryPath, type PortableLayout } from '../../shared/portable';
import { log } from '../log';
import { createWhisperEngine, detectWhisper, type WhisperOptions } from './whisper';

/** Where the compiled Swift helper is looked for, in order. */
export function helperCandidates(appRoot: string, resourcesPath: string): string[] {
  return [
    path.join(resourcesPath, 'ornith-stt'), // packaged app
    path.join(appRoot, 'native', 'build', 'ornith-stt'), // built from source
  ];
}

export function findHelper(candidates: string[]): string | null {
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

export interface SttEngine {
  availability(): EngineAvailability;
  transcribe(request: TranscriptionRequest): Promise<TranscriptionResult>;
}

export interface SttOptions {
  appRoot: string;
  resourcesPath: string;
  /** Overridable so tests can exercise resolution without a real binary. */
  platform?: NodeJS.Platform;
  arch?: string;
  /** The drive's layout in portable mode; null for an ordinary install. */
  layout?: PortableLayout | null;
}

/* ------------------------------------------------------------ macOS Speech */

export function detectAppleSpeech(options: SttOptions): EngineAvailability {
  const platform = options.platform ?? process.platform;
  const engine = 'macos-speech';

  if (platform !== 'darwin') {
    return { available: false, engine, reason: 'On-device speech recognition requires macOS.' };
  }

  if (!findHelper(helperCandidates(options.appRoot, options.resourcesPath))) {
    return {
      available: false,
      engine,
      reason:
        'The speech helper has not been built yet. Run `npm run build:stt` (requires Xcode Command Line Tools).',
    };
  }

  return { available: true, engine };
}

async function transcribeWithHelper(
  helper: string,
  request: TranscriptionRequest,
): Promise<TranscriptionResult> {
  // The helper reads a file rather than a stream: the Speech framework's
  // file-based request is the reliable path, and a temp file keeps the
  // audio on disk only for the duration of the call.
  const dir = mkdtempSync(path.join(tmpdir(), 'ornith-stt-'));
  const wavPath = path.join(dir, 'input.wav');

  try {
    writeFileSync(wavPath, request.wav);

    const stdout = await new Promise<string>((resolve, reject) => {
      execFile(
        helper,
        [wavPath, request.locale],
        { timeout: 30_000, maxBuffer: 1024 * 1024 },
        (err, out, errOut) => {
          if (err) return reject(new Error(errOut?.trim() || err.message));
          resolve(out);
        },
      );
    });

    const parsed = JSON.parse(stdout.trim()) as { text?: string; error?: string };
    if (parsed.error) return { text: '', error: parsed.error };
    return { text: (parsed.text ?? '').trim() };
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    log.warn('stt.failed', { detail });
    return { text: '', error: `Speech recognition failed: ${detail}` };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/* ---------------------------------------------------------------- dispatch */

/**
 * Whisper only exists when there is a drive to carry it. An installed app gets
 * a null binary path and so reports whisper unavailable, which is correct: it
 * has nowhere to have put one.
 */
export function whisperOptionsFor(options: SttOptions): WhisperOptions {
  const layout = options.layout;
  if (!layout) return { binaryPath: null, modelDir: '' };

  return {
    binaryPath: toolBinaryPath(
      layout,
      options.platform ?? process.platform,
      options.arch ?? process.arch,
      'whisper',
    ),
    modelDir: layout.sttModelDir,
  };
}

export function detectStt(options: SttOptions): EngineAvailability {
  const apple = detectAppleSpeech(options);
  if (apple.available) return apple;

  const whisper = detectWhisper(whisperOptionsFor(options));
  if (whisper.available) return whisper;

  // Neither runs. Report whichever failure the user can actually act on: on a
  // drive that is the missing whisper build, because compiling a Swift helper
  // is not something you do to a USB stick; on an installed Mac it is the
  // helper, which is one command away.
  const platform = options.platform ?? process.platform;
  if (options.layout) return whisper;
  if (platform === 'darwin') return apple;

  // Installed, off macOS: there is no drive, so whisper's reason would be
  // talking about one that does not exist.
  return {
    ...whisper,
    reason:
      'Dictation needs an on-device speech engine. macOS provides one; ' +
      'elsewhere the Ornith Portable drive carries one.',
  };
}

export function createSttEngine(options: SttOptions): SttEngine {
  return {
    availability: () => detectStt(options),

    async transcribe(request): Promise<TranscriptionResult> {
      if (detectAppleSpeech(options).available) {
        const helper = findHelper(helperCandidates(options.appRoot, options.resourcesPath));
        if (helper) return transcribeWithHelper(helper, request);
      }

      const whisper = whisperOptionsFor(options);
      if (detectWhisper(whisper).available) {
        return createWhisperEngine(whisper).transcribe(request);
      }

      return { text: '', error: detectStt(options).reason };
    },
  };
}
