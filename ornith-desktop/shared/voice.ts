/** Voice layer types, shared by main, preload, and renderer. */

export interface EngineAvailability {
  available: boolean;
  /** Identifier of the concrete engine, e.g. 'macos-speech' or 'macos-say'. */
  engine: string;
  /** Plain-English reason shown to the user when unavailable. */
  reason?: string;
}

export interface VoiceCapabilities {
  stt: EngineAvailability;
  tts: EngineAvailability & { voices: string[] };
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

/** Sample rate the Speech framework expects; also keeps payloads small. */
export const STT_SAMPLE_RATE = 16_000;

/** Guard against a runaway recording filling memory. */
export const MAX_RECORDING_SECONDS = 60;
