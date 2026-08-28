/**
 * Speech-to-text with whisper.cpp, from a binary the drive carries.
 *
 * The macOS Speech framework is better where it exists — it is already on the
 * machine, needs no model on the drive, and is faster. But a portable drive
 * gets plugged into Windows and Linux too, and there it is the only local
 * option that does not send audio to somebody's server. So this sits behind
 * the same `SttEngine` interface and takes over wherever the Apple path
 * cannot run.
 *
 * whisper.cpp is a single binary plus a GGML model file. Both live on the
 * drive, so transcription works on a machine that has never had either.
 */
import { execFile } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { languageOf, type EngineAvailability, type TranscriptionResult } from '../../shared/voice';
import { log } from '../log';

/** Whisper is slower than real time on weak hardware; a minute of audio is the cap. */
export const WHISPER_TIMEOUT_MS = 120_000;

export interface WhisperOptions {
  /** Absolute path to the whisper-cli binary, or null when the drive has none. */
  binaryPath: string | null;
  /** Directory holding GGML models, e.g. `ggml-base.en.bin`. */
  modelDir: string;
}

/**
 * Any `.bin` in the model directory. Whisper models have no manifest and no
 * fixed name — a user may drop in `ggml-tiny.en.bin` or a quantised variant —
 * so the rule is the extension, and the smallest file wins as the default
 * because that is the one most likely to keep up on the host it is plugged
 * into.
 */
export function listWhisperModels(modelDir: string): string[] {
  try {
    return readdirSync(modelDir)
      .filter((name) => name.toLowerCase().endsWith('.bin'))
      .sort();
  } catch {
    return [];
  }
}

export function resolveModel(modelDir: string): string | null {
  const [first] = listWhisperModels(modelDir);
  return first ? path.join(modelDir, first) : null;
}

export function detectWhisper(options: WhisperOptions): EngineAvailability {
  const engine = 'whisper-cpp';

  if (!options.binaryPath || !existsSync(options.binaryPath)) {
    return {
      available: false,
      engine,
      reason:
        'This drive carries no speech-to-text engine for this computer. ' +
        'Add a whisper.cpp build to the drive to dictate here.',
    };
  }

  if (!resolveModel(options.modelDir)) {
    return {
      available: false,
      engine,
      reason: `No speech model found. Put a whisper .bin model in ${options.modelDir}.`,
    };
  }

  return { available: true, engine };
}

/**
 * `-nt` drops timestamps, `-otxt`/`-of` write the transcript to a file rather
 * than leaving it to be scraped out of stdout — whisper.cpp interleaves
 * progress and model banners there, and parsing that is how transcripts end up
 * containing load messages.
 */
export function whisperArgs(
  modelPath: string,
  wavPath: string,
  outputBase: string,
  locale: string,
): string[] {
  return [
    '--model', modelPath,
    '--file', wavPath,
    '--language', languageOf(locale),
    '--output-txt',
    '--output-file', outputBase,
    '--no-timestamps',
    // One thread per core is the default and oversubscribes a laptop that is
    // also running the model; four is enough to keep up with short dictation.
    '--threads', '4',
  ];
}

/**
 * whisper.cpp marks non-speech with bracketed tags like `[BLANK_AUDIO]` or
 * `(speaking in foreign language)`. They are annotations, not words, and
 * putting them in the composer would look like the app inventing text.
 */
export function cleanTranscript(raw: string): string {
  return raw
    .replace(/\[[A-Z_ ]+\]/g, ' ')
    .replace(/\((?:blank|silence|music|inaudible)[^)]*\)/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export interface WhisperEngine {
  availability(): EngineAvailability;
  transcribe(request: { wav: Uint8Array; locale: string }): Promise<TranscriptionResult>;
}

export function createWhisperEngine(options: WhisperOptions): WhisperEngine {
  return {
    availability: () => detectWhisper(options),

    async transcribe(request): Promise<TranscriptionResult> {
      const detection = detectWhisper(options);
      if (!detection.available) return { text: '', error: detection.reason };

      const binary = options.binaryPath;
      const model = resolveModel(options.modelDir);
      if (!binary || !model) return { text: '', error: 'The speech engine went missing.' };

      // A temp directory keeps the audio on disk only for the length of the
      // call, and gives the transcript file somewhere to land.
      const dir = mkdtempSync(path.join(tmpdir(), 'ornith-whisper-'));
      const wavPath = path.join(dir, 'input.wav');
      const outputBase = path.join(dir, 'out');

      try {
        writeFileSync(wavPath, request.wav);

        await new Promise<void>((resolve, reject) => {
          execFile(
            binary,
            whisperArgs(model, wavPath, outputBase, request.locale),
            { timeout: WHISPER_TIMEOUT_MS, maxBuffer: 4 * 1024 * 1024 },
            (err, _stdout, stderr) => {
              if (err) {
                const detail = stderr?.trim().split('\n').pop() ?? err.message;
                return reject(new Error(detail || err.message));
              }
              resolve();
            },
          );
        });

        const transcriptPath = `${outputBase}.txt`;
        if (!existsSync(transcriptPath)) {
          return { text: '', error: 'The speech engine produced no transcript.' };
        }

        return { text: cleanTranscript(readFileSync(transcriptPath, 'utf8')) };
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        log.warn('stt.whisper.failed', { detail });
        return { text: '', error: `Speech recognition failed: ${detail}` };
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    },
  };
}
