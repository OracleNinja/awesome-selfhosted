/**
 * Text-to-speech via macOS `say`.
 *
 * Chosen over the Web Speech API because a child process can be killed
 * outright, which is what makes the stop control genuinely immediate. Runs
 * entirely on-device; no audio or text leaves the machine.
 */
import { spawn, execFile, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import type { EngineAvailability, SpeakRequest } from '../../shared/voice';
import { log } from '../log';

const SAY_BINARY = '/usr/bin/say';

export interface TtsEngine {
  availability(): EngineAvailability;
  listVoices(): Promise<string[]>;
  speak(request: SpeakRequest, onFinished: (requestId: string) => void): void;
  stop(): void;
  readonly speakingRequestId: string | null;
}

export function detectTts(): EngineAvailability {
  if (process.platform !== 'darwin') {
    return {
      available: false,
      engine: 'macos-say',
      reason: 'Spoken responses require macOS.',
    };
  }
  if (!existsSync(SAY_BINARY)) {
    return {
      available: false,
      engine: 'macos-say',
      reason: 'The macOS speech tool (/usr/bin/say) was not found.',
    };
  }
  return { available: true, engine: 'macos-say' };
}

export function createTtsEngine(): TtsEngine {
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
    availability: detectTts,

    async listVoices(): Promise<string[]> {
      if (!detectTts().available) return [];
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
    },

    speak(request, onFinished) {
      stop(); // one utterance at a time

      const detection = detectTts();
      if (!detection.available || !request.text.trim()) {
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

    stop,

    get speakingRequestId() {
      return currentId;
    },
  };
}
