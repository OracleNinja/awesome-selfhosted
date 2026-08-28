import { test, expect, _electron as electron, type ElectronApplication, type Page } from '@playwright/test';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { startStubOllama, type StubHandle } from '../integration/stubOllama';
import { resolveLayout, toolBinaryPath, type PortableLayout } from '../../shared/portable';
import type { VoiceCapabilities } from '../../shared/voice';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

/**
 * A drive gets plugged into whatever is to hand. These tests provision one with
 * stand-in speech binaries and drive the real app against them, so the claim
 * "voice works away from macOS" is checked against actual spawned processes
 * rather than asserted.
 */
type OrnithWindow = {
  ornith: {
    on(channel: string, callback: (payload: unknown) => void): () => void;
    voice: {
      capabilities(): Promise<VoiceCapabilities>;
      transcribe(wav: Uint8Array, locale: string): Promise<{ text: string; error?: string }>;
      speak(request: { requestId: string; text: string; voice: string; rate: number }): Promise<void>;
      stopSpeaking(): Promise<void>;
    };
  };
};

let stub: StubHandle;
let app: ElectronApplication;
let page: Page;
let drive: string;
let root: string;

function launchEnv(extra: Record<string, string> = {}): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  delete env.ORNITH_USER_DATA;

  return {
    ...env,
    ORNITH_PORTABLE_ROOT: root,
    ORNITH_OLLAMA_URL: stub.url,
    NODE_ENV: 'test',
    ...extra,
  };
}

async function open(): Promise<void> {
  app = await electron.launch({
    args: [appRoot, '--no-sandbox', '--disable-gpu'],
    env: launchEnv(),
  });
  page = await app.firstWindow();
  await page.waitForSelector('.app', { timeout: 30_000 });

  const chooser = page.getByTestId('mode-chooser');
  if (await chooser.isVisible().catch(() => false)) {
    await page.getByTestId('choose-local').click();
    await expect(chooser).toHaveCount(0);
  }
}

const capabilities = (): Promise<VoiceCapabilities> =>
  page.evaluate(() => (window as unknown as OrnithWindow).ornith.voice.capabilities());

function shellBinary(target: string, body: string): void {
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, body);
  chmodSync(target, 0o755);
}

/** A stand-in for whisper-cli: writes a transcript where the real one would. */
function installWhisper(layout: PortableLayout, transcript = 'dictated from the drive'): void {
  shellBinary(
    toolBinaryPath(layout, process.platform, process.arch, 'whisper'),
    '#!/bin/sh\n' +
      'while [ $# -gt 0 ]; do\n' +
      '  if [ "$1" = "--output-file" ]; then out="$2"; fi\n' +
      '  shift\n' +
      'done\n' +
      `printf '%s\\n' "${transcript}" > "$out.txt"\n`,
  );

  mkdirSync(layout.sttModelDir, { recursive: true });
  writeFileSync(path.join(layout.sttModelDir, 'ggml-base.en.bin'), 'model');
}

/**
 * A stand-in for piper. Writes a real, decodable WAV — the renderer runs it
 * through Web Audio, which rejects anything that is not one.
 */
function installPiper(layout: PortableLayout, delaySeconds = 0): void {
  shellBinary(
    toolBinaryPath(layout, process.platform, process.arch, 'piper'),
    '#!/bin/sh\n' +
      (delaySeconds > 0 ? `sleep ${delaySeconds}\n` : '') +
      'while [ $# -gt 0 ]; do\n' +
      '  if [ "$1" = "--output_file" ]; then out="$2"; fi\n' +
      '  shift\n' +
      'done\n' +
      'cat > /dev/null\n' +
      // 8000 Hz, 16-bit mono, 400 samples of silence: a valid RIFF/WAVE header
      // written byte by byte, so no fixture file is needed.
      'printf "RIFF" > "$out"\n' +
      'printf "\\104\\003\\000\\000WAVEfmt " >> "$out"\n' +
      'printf "\\020\\000\\000\\000\\001\\000\\001\\000" >> "$out"\n' +
      'printf "\\100\\037\\000\\000\\200\\076\\000\\000" >> "$out"\n' +
      'printf "\\002\\000\\020\\000data" >> "$out"\n' +
      'printf "\\040\\003\\000\\000" >> "$out"\n' +
      'dd if=/dev/zero bs=1 count=800 >> "$out" 2>/dev/null\n',
  );

  mkdirSync(layout.ttsVoiceDir, { recursive: true });
  writeFileSync(path.join(layout.ttsVoiceDir, 'en_US-test-medium.onnx'), 'voice');
  writeFileSync(path.join(layout.ttsVoiceDir, 'en_US-test-medium.onnx.json'), '{}');
}

test.beforeEach(async () => {
  drive = mkdtempSync(path.join(tmpdir(), 'ornith-voice-'));
  root = path.join(drive, 'Ornith');
  stub = await startStubOllama();
});

test.afterEach(async () => {
  await app?.close().catch(() => {});
  await stub?.close();
  rmSync(drive, { recursive: true, force: true });
});

test('a drive with no speech engine says so, and typing still works', async () => {
  await open();

  const caps = await capabilities();
  expect(caps.stt.available).toBe(false);
  expect(caps.tts.available).toBe(false);

  // The reason has to name the drive, not somebody else's operating system.
  expect(caps.stt.reason).not.toMatch(/requires macOS/i);
  expect(caps.stt.reason).toMatch(/drive|whisper/i);
  expect(caps.tts.reason).toMatch(/drive|piper/i);

  await expect(page.getByTestId('mic-button')).toBeDisabled();
  await expect(page.getByTestId('mic-button')).toHaveAttribute('title', caps.stt.reason!);

  await page.getByTestId('composer-input').fill('typing still fine');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world', {
    timeout: 20_000,
  });
});

test('dictation works off a drive on a machine with no Apple speech framework', async () => {
  installWhisper(resolveLayout(root));
  await open();

  const caps = await capabilities();
  expect(caps.stt.available).toBe(true);
  expect(caps.stt.engine).toBe('whisper-cpp');
  await expect(page.getByTestId('mic-button')).toBeEnabled();

  // Straight through the real IPC path and a real spawned binary.
  const result = await page.evaluate(() =>
    (window as unknown as OrnithWindow).ornith.voice.transcribe(new Uint8Array([1, 2, 3]), 'en-US'),
  );

  expect(result.error).toBeUndefined();
  expect(result.text).toBe('dictated from the drive');
});

test('an engine but no microphone fails visibly and leaves the app usable', async () => {
  // The engine is present, so the button is live — but this machine has no
  // microphone, which is an ordinary state for a drive plugged into a desktop.
  installWhisper(resolveLayout(root));
  await open();

  await expect(page.getByTestId('mic-button')).toBeEnabled();
  await page.getByTestId('mic-button').click();

  // It must come back to idle rather than wedging in `recording`, and say why.
  await expect(page.getByTestId('mic-button')).toHaveAttribute('data-state', 'idle', {
    timeout: 30_000,
  });
  const error = page.getByTestId('voice-error');
  await expect(error).toBeVisible({ timeout: 15_000 });
  // Never the macOS panel name on a machine that has no such panel.
  await expect(error).not.toContainText('System Settings');

  await page.getByTestId('composer-input').fill('typed after a failed dictation');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world', {
    timeout: 20_000,
  });
});

test('spoken replies work off a drive, synthesised in main and played in the renderer', async () => {
  installPiper(resolveLayout(root));
  await open();

  const caps = await capabilities();
  expect(caps.tts.available).toBe(true);
  expect(caps.tts.engine).toBe('piper');
  // The drive's engine only writes a file; the speakers belong to the renderer.
  expect(caps.tts.playback).toBe('renderer');
  expect(caps.tts.voices).toEqual(['en_US-test-medium']);

  // Speak, and wait for main to report it finished. That only happens after the
  // renderer has decoded the WAV and played it back, so a single assertion
  // covers synthesis, IPC transfer, Web Audio decoding and the round trip.
  const spoken = await page.evaluate(async () => {
    const win = window as unknown as OrnithWindow;

    return new Promise<{ audio: boolean; finished: boolean }>((resolve) => {
      let audio = false;
      const timer = setTimeout(() => resolve({ audio, finished: false }), 25_000);

      const offAudio = win.ornith.on('tts:audio', () => {
        audio = true;
      });
      const offState = win.ornith.on('tts:state', (payload) => {
        const state = payload as { speaking: boolean; requestId: string | null };
        if (state.speaking || state.requestId !== 'e2e-speak') return;
        clearTimeout(timer);
        offAudio();
        offState();
        resolve({ audio, finished: true });
      });

      void win.ornith.voice.speak({
        requestId: 'e2e-speak',
        text: 'Hello from the drive.',
        voice: '',
        rate: 175,
      });
    });
  });

  expect(spoken.audio).toBe(true);
  expect(spoken.finished).toBe(true);
});

test('a voice missing its config is refused rather than offered and failing later', async () => {
  const layout = resolveLayout(root);
  shellBinary(toolBinaryPath(layout, process.platform, process.arch, 'piper'), '#!/bin/sh\nexit 0\n');
  mkdirSync(layout.ttsVoiceDir, { recursive: true });
  writeFileSync(path.join(layout.ttsVoiceDir, 'orphan.onnx'), 'voice');

  await open();

  const caps = await capabilities();
  expect(caps.tts.available).toBe(false);
  expect(caps.tts.voices).toEqual([]);
  expect(caps.tts.reason).toContain(layout.ttsVoiceDir);
});

test('asking for speech with no engine resolves quietly instead of hanging', async () => {
  await open();

  const finished = await page.evaluate(async () => {
    const win = window as unknown as OrnithWindow;

    return new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => resolve(false), 15_000);
      const off = win.ornith.on('tts:state', (payload) => {
        const state = payload as { speaking: boolean; requestId: string | null };
        if (state.speaking || state.requestId !== 'e2e-none') return;
        clearTimeout(timer);
        off();
        resolve(true);
      });

      void win.ornith.voice.speak({
        requestId: 'e2e-none',
        text: 'nobody can hear this',
        voice: '',
        rate: 175,
      });
    });
  });

  // The indicator must come back down, or the UI shows "speaking" forever.
  expect(finished).toBe(true);
});

test('stopping during synthesis keeps the reply from being spoken afterwards', async () => {
  // Synthesis is slow enough here to press Stop inside it — the real window on
  // a USB drive, where Piper takes seconds on a long reply.
  installPiper(resolveLayout(root), 3);
  await open();

  const outcome = await page.evaluate(async () => {
    const win = window as unknown as OrnithWindow;

    return new Promise<{ audio: boolean; quiet: boolean }>((resolve) => {
      let audio = false;
      let quiet = false;

      const offAudio = win.ornith.on('tts:audio', () => {
        audio = true;
      });
      const offState = win.ornith.on('tts:state', (payload) => {
        const state = payload as { speaking: boolean; requestId: string | null };
        if (!state.speaking) quiet = true;
      });

      void win.ornith.voice.speak({
        requestId: 'e2e-stopped',
        text: 'this should never be heard',
        voice: '',
        rate: 175,
      });

      // Stop well before the synthesiser finishes.
      setTimeout(() => void win.ornith.voice.stopSpeaking(), 300);

      // Then wait past the point the audio would otherwise have arrived.
      setTimeout(() => {
        offAudio();
        offState();
        resolve({ audio, quiet });
      }, 8000);
    });
  });

  // The audio must never reach the renderer, and the indicator must be down.
  expect(outcome.audio).toBe(false);
  expect(outcome.quiet).toBe(true);
});
