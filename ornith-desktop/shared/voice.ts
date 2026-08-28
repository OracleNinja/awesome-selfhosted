/** Voice layer types, shared by main, preload, and renderer. */

export interface EngineAvailability {
  available: boolean;
  /** Identifier of the concrete engine, e.g. 'macos-speech' or 'macos-say'. */
  engine: string;
  /** Plain-English reason shown to the user when unavailable. */
  reason?: string;
}

/**
 * Where the audio for a spoken reply is produced.
 *
 * `native` — the engine talks to the speakers itself (macOS `say`). Speech
 * starts on the first word, and killing the process stops it instantly.
 *
 * `renderer` — the engine writes a WAV and the renderer plays it through Web
 * Audio. This is how a drive-bundled engine works on Windows and Linux, where
 * there is no dependable system audio player to shell out to. Decoding an
 * ArrayBuffer needs no blob or data URL, so the strict CSP is untouched.
 */
export type TtsPlayback = 'native' | 'renderer';

export interface VoiceCapabilities {
  stt: EngineAvailability;
  tts: EngineAvailability & { voices: string[]; playback: TtsPlayback };
}

export interface TranscriptionRequest {
  /** 16-bit PCM WAV bytes. */
  wav: Uint8Array;
  /** BCP-47 tag, e.g. 'en-US'. */
  locale: string;
}

export interface TranscriptionResult {
  text: string;
  /** Present when transcription failed; `text` is then empty. */
  error?: string;
}

export interface SpeakRequest {
  requestId: string;
  text: string;
  /** Empty string means the system default voice. */
  voice: string;
  /** Words per minute; `say` defaults to about 175. */
  rate: number;
}

export interface TtsState {
  speaking: boolean;
  requestId: string | null;
}

/** Sent to the renderer to play, when the engine's playback mode is `renderer`. */
export interface SpokenAudio {
  requestId: string;
  /** A complete RIFF/WAVE file. */
  wav: Uint8Array;
}

export interface SynthesisResult {
  wav: Uint8Array | null;
  /** Present when synthesis failed; `wav` is then null. */
  error?: string;
}

/** Sample rate the Speech framework expects; also keeps payloads small. */
export const STT_SAMPLE_RATE = 16_000;

/** Guard against a runaway recording filling memory. */
export const MAX_RECORDING_SECONDS = 60;

/**
 * `en-US` → `en`. whisper.cpp and Piper both take a bare ISO-639-1 code, while
 * the settings store and the macOS Speech framework use BCP-47.
 */
export function languageOf(locale: string): string {
  const base = locale.trim().split(/[-_]/)[0].toLowerCase();
  return /^[a-z]{2,3}$/.test(base) ? base : 'en';
}
