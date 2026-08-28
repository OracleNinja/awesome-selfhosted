/**
 * Text-to-speech, dispatched across two on-device engines.
 *
 *   `macos-say` — /usr/bin/say. It talks to the speakers itself, so speech
 *   starts on the first word and killing the process stops it instantly.
 *   Preferred wherever it exists.
 *
 *   `piper` — a binary and a voice the drive carries. This is what gives a
 *   Windows or Linux machine spoken replies at all. Piper writes a WAV rather
 *   than playing it, so the renderer does the playing; see TtsPlayback.
 *
 * Both run entirely on-device; no audio or text leaves the machine.
 */
import { spawn, execFile, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';

import type {
  EngineAvailability,
  SpeakRequest,
  SynthesisResult,
  TtsPlayback,
} from '../../shared/voice';
import { toolBinaryPath, type PortableLayout } from '../../shared/portable';
import { createPiperEngine, detectPiper, listPiperVoices, type PiperOptions } from './piper';
import { log } from '../log';

const SAY_BINARY = '/usr/bin/say';

export type TtsAvailability = EngineAvailability & { playback: TtsPlayback };

export interface TtsOptions {
  /** The drive's layout in portable mode; null for an ordinary install. */
  layout?: PortableLayout | null;
  /** Overridable so tests can exercise resolution without a real binary. */
  platform?: NodeJS.Platform;
  arch?: string;
}

export interface TtsEngine {
  availability(): TtsAvailability;
  listVoices(): Promise<string[]>;
  /** Only meaningful when `availability().playback` is `native`. */
  speak(request: SpeakRequest, onFinished: (requestId: string) => void): void;
  /** Only meaningful when `availability().playback` is `renderer`. */
  synthesize(request: SpeakRequest): Promise<SynthesisResult>;
  stop(): void;
  readonly speakingRequestId: string | null;
}

/* ----------------------------------------------------------------- macOS */

export function detectSay(platform: NodeJS.Platform = process.platform): EngineAvailability {
  const engine = 'macos-say';

  if (platform !== 'darwin') {
    return { available: false, engine, reason: 'The system speech tool is macOS-only.' };
  }
  if (!existsSync(SAY_BINARY)) {
    return { available: false, engine, reason: 'The macOS speech tool was not found.' };
  }
  return { available: true, engine };
}

async function listSayVoices(): Promise<string[]> {
  return new Promise((resolve) => {
    execFile(SAY_BINARY, ['-v', '?'], { timeout: 5000 }, (err, stdout) => {
      if (err) return resolve([]);
      // Lines look like: "Samantha           en_US    # Hello, my name is..."
      const voices = stdout
        .split('\n')
        .map((line) => /^(\S+(?: \S+)*?)\s{2,}([a-z]{2}_[A-Z]{2})/.exec(line))
        .filter((m): m is RegExpExecArray => m !== null)
        .map((m) => m[1].trim());
      resolve([...new Set(voices)]);
    });
  });
}

/* --------------------------------------------------------------- dispatch */

/**
 * Piper only exists when there is a drive to carry it. An installed app gets a
 * null binary path and so reports it unavailable, which is correct: it has
 * nowhere to have put one.
 */
export function piperOptionsFor(options: TtsOptions): PiperOptions {
  const layout = options.layout;
  if (!layout) return { binaryPath: null, voiceDir: '' };

  return {
    binaryPath: toolBinaryPath(
      layout,
      options.platform ?? process.platform,
      options.arch ?? process.arch,
      'piper',
    ),
    voiceDir: layout.ttsVoiceDir,
  };
}

export function detectTts(options: TtsOptions = {}): TtsAvailability {
  const platform = options.platform ?? process.platform;

  const say = detectSay(platform);
  if (say.available) return { ...say, playback: 'native' };

  const piper = detectPiper(piperOptionsFor(options));
  if (piper.available) return { ...piper, playback: 'renderer' };

  // Neither runs. Report whichever failure the user can act on: on a drive
  // that is the missing Piper build; on an installed Mac it is `say` itself,
  // whose absence is a broken system rather than a missing download.
  if (options.layout || platform !== 'darwin') return { ...piper, playback: 'renderer' };
  return { ...say, playback: 'native' };
}

export function createTtsEngine(options: TtsOptions = {}): TtsEngine {
  let child: ChildProcess | null = null;
  let currentId: string | null = null;

  function stop(): void {
    if (!child) return;
    const dying = child;
    child = null;
    currentId = null;
    // SIGTERM is enough for `say`; it stops the audio immediately.
    try {
      dying.kill('SIGTERM');
    } catch {
      /* already gone */
    }
  }

  return {
    availability: () => detectTts(options),

    async listVoices(): Promise<string[]> {
      const detection = detectTts(options);
      if (!detection.available) return [];
      if (detection.engine === 'macos-say') return listSayVoices();
      return listPiperVoices(piperOptionsFor(options).voiceDir);
    },

    speak(request, onFinished) {
      stop(); // one utterance at a time

      const detection = detectTts(options);
      if (!detection.available || detection.playback !== 'native' || !request.text.trim()) {
        onFinished(request.requestId);
        return;
      }

      const args: string[] = [];
      if (request.voice) args.push('-v', request.voice);
      if (Number.isFinite(request.rate) && request.rate > 0) {
        args.push('-r', String(Math.round(request.rate)));
      }

      // Text goes over stdin rather than argv: an answer can exceed the argv
      // limit, and stdin avoids any quoting question entirely.
      const proc = spawn(SAY_BINARY, args, { stdio: ['pipe', 'ignore', 'pipe'] });
      child = proc;
      currentId = request.requestId;

      proc.on('error', (err) => {
        log.warn('tts.spawn.failed', { detail: String(err) });
        if (child === proc) {
          child = null;
          currentId = null;
        }
        onFinished(request.requestId);
      });

      proc.on('close', () => {
        if (child === proc) {
          child = null;
          currentId = null;
        }
        onFinished(request.requestId);
      });

      proc.stdin?.on('error', () => {
        /* the process may exit before the write drains */
      });
      proc.stdin?.write(request.text);
      proc.stdin?.end();
    },

    async synthesize(request): Promise<SynthesisResult> {
      const detection = detectTts(options);
      if (!detection.available || detection.playback !== 'renderer') {
        return { wav: null, error: detection.reason ?? 'Spoken replies are unavailable.' };
      }

      // Tracked so `stop()` and the speaking indicator behave the same way for
      // both engines, even though nothing local is playing.
      currentId = request.requestId;
      try {
        return await createPiperEngine(piperOptionsFor(options)).synthesize({
          text: request.text,
          voice: request.voice,
          rate: request.rate,
        });
      } finally {
        if (currentId === request.requestId) currentId = null;
      }
    },

    stop,

    get speakingRequestId() {
      return currentId;
    },
  };
}
