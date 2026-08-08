/**
 * Speech-to-text via the macOS Speech framework, on-device.
 *
 * Deliberately NOT the Web Speech API: Chromium's webkitSpeechRecognition
 * streams microphone audio to Google's servers, which would violate the
 * local-only requirement while looking like a browser API.
 *
 * The concrete engine sits behind SttEngine so whisper.cpp can be added later
 * without touching the IPC contract or the UI.
 */
import { execFile } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { EngineAvailability, TranscriptionRequest, TranscriptionResult } from '../../shared/voice';
import { log } from '../log';

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
}

export function detectStt(options: SttOptions): EngineAvailability {
  const platform = options.platform ?? process.platform;

  if (platform !== 'darwin') {
    return {
      available: false,
      engine: 'macos-speech',
      reason: 'On-device speech recognition requires macOS.',
    };
  }

  const helper = findHelper(helperCandidates(options.appRoot, options.resourcesPath));
  if (!helper) {
    return {
      available: false,
      engine: 'macos-speech',
      reason:
        'The speech helper has not been built yet. Run `npm run build:stt` (requires Xcode Command Line Tools).',
    };
  }

  return { available: true, engine: 'macos-speech' };
}

export function createSttEngine(options: SttOptions): SttEngine {
  return {
    availability: () => detectStt(options),

    async transcribe(request): Promise<TranscriptionResult> {
      const detection = detectStt(options);
      if (!detection.available) {
        return { text: '', error: detection.reason };
      }

      const helper = findHelper(helperCandidates(options.appRoot, options.resourcesPath));
      if (!helper) return { text: '', error: 'The speech helper is missing.' };

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
    },
  };
}
