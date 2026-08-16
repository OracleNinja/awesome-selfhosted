/**
 * Voice providers.
 *
 * Two interfaces, two implementations each:
 *
 *  - `browser`  — the work happens client-side via the Web Speech API. The
 *                 server-side object exists so the rest of JARVIS can ask
 *                 "is voice available and where does it run?" without caring.
 *  - `nvidia`   — real HTTP calls to a configured NVIDIA Riva / NIM speech
 *                 endpoint. Unavailable, with a reason, until configured.
 *
 * The orchestrator never imports either implementation directly; it only sees
 * SpeechToTextProvider / TextToSpeechProvider.
 */
import type { JarvisConfig, ProviderStatus } from '@jarvis/shared';
import { fetchWithTimeout, safeHost, truncate } from '@jarvis/shared';

export type VoiceMode = 'server' | 'browser';

export interface TranscriptionResult {
  text: string;
  model: string;
  durationMs: number;
}

export interface SynthesisResult {
  /** base64-encoded audio. */
  audioB64: string;
  mimeType: string;
  model: string;
}

export interface SpeechToTextProvider {
  readonly id: string;
  /** `browser` means the client performs capture *and* recognition. */
  readonly mode: VoiceMode;
  isAvailable(): boolean;
  status(): ProviderStatus;
  transcribe(input: {
    audioB64: string;
    mimeType: string;
    languageCode?: string;
    signal?: AbortSignal;
  }): Promise<TranscriptionResult>;
}

export interface TextToSpeechProvider {
  readonly id: string;
  readonly mode: VoiceMode;
  isAvailable(): boolean;
  status(): ProviderStatus;
  synthesize(input: { text: string; voice?: string; signal?: AbortSignal }): Promise<SynthesisResult>;
}

export class VoiceUnavailableError extends Error {
  readonly providerId: string;
  constructor(providerId: string, message: string) {
    super(message);
    this.name = 'VoiceUnavailableError';
    this.providerId = providerId;
  }
}

// ---------------------------------------------------------------------------
// Browser (client-side) providers
// ---------------------------------------------------------------------------

const BROWSER_STT_NOTE =
  'Speech-to-text runs in the browser via the Web Speech API. The server does not transcribe audio in this mode.';
const BROWSER_TTS_NOTE =
  'Text-to-speech runs in the browser via speechSynthesis. The server does not synthesise audio in this mode.';

export class BrowserSpeechToTextProvider implements SpeechToTextProvider {
  readonly id = 'browser:stt';
  readonly mode: VoiceMode = 'browser';

  isAvailable(): boolean {
    return true;
  }

  status(): ProviderStatus {
    return { id: this.id, kind: 'stt', available: true, reason: BROWSER_STT_NOTE };
  }

  async transcribe(): Promise<TranscriptionResult> {
    throw new VoiceUnavailableError(this.id, BROWSER_STT_NOTE);
  }
}

export class BrowserTextToSpeechProvider implements TextToSpeechProvider {
  readonly id = 'browser:tts';
  readonly mode: VoiceMode = 'browser';

  isAvailable(): boolean {
    return true;
  }

  status(): ProviderStatus {
    return { id: this.id, kind: 'tts', available: true, reason: BROWSER_TTS_NOTE };
  }

  async synthesize(): Promise<SynthesisResult> {
    throw new VoiceUnavailableError(this.id, BROWSER_TTS_NOTE);
  }
}

// ---------------------------------------------------------------------------
// NVIDIA (server-side) providers
// ---------------------------------------------------------------------------

/**
 * NVIDIA Riva / NIM speech-to-text over HTTP.
 *
 * Expects a JSON endpoint accepting `{ audio, language_code, model }` with
 * base64 audio and returning a transcript. NIM deployments differ in their
 * exact field names; NVIDIA_STT_URL points at whichever route your deployment
 * exposes, and the response reader below accepts the common variants.
 */
export class NvidiaSpeechToTextProvider implements SpeechToTextProvider {
  readonly id = 'nvidia:stt';
  readonly mode: VoiceMode = 'server';

  constructor(
    private readonly url: string,
    private readonly model: string,
    private readonly apiKey: string,
  ) {}

  private unavailableReason(): string | null {
    if (!this.url) return 'nvidia:stt: NVIDIA_STT_URL is not configured';
    if (!this.apiKey) return 'nvidia:stt: NVIDIA_API_KEY is not configured';
    return null;
  }

  isAvailable(): boolean {
    return this.unavailableReason() === null;
  }

  status(): ProviderStatus {
    const reason = this.unavailableReason();
    const status: ProviderStatus = {
      id: this.id,
      kind: 'stt',
      available: reason === null,
      model: this.model,
      endpoint: safeHost(this.url),
    };
    if (reason) status.reason = reason;
    return status;
  }

  async transcribe(input: {
    audioB64: string;
    mimeType: string;
    languageCode?: string;
    signal?: AbortSignal;
  }): Promise<TranscriptionResult> {
    const reason = this.unavailableReason();
    if (reason) throw new VoiceUnavailableError(this.id, reason);

    const startedAt = Date.now();
    const response = await fetchWithTimeout(this.url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
        authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify({
        audio: input.audioB64,
        language_code: input.languageCode ?? 'en-US',
        ...(this.model ? { model: this.model } : {}),
        encoding: input.mimeType,
      }),
      timeoutMs: 120_000,
      ...(input.signal ? { signal: input.signal } : {}),
    });

    const text = await response.text();
    if (!response.ok) {
      throw new VoiceUnavailableError(this.id, `HTTP ${response.status}: ${truncate(text, 300)}`);
    }
    const payload = JSON.parse(text) as {
      text?: string;
      transcript?: string;
      results?: { alternatives?: { transcript?: string }[] }[];
    };
    const transcript =
      payload.text ?? payload.transcript ?? payload.results?.[0]?.alternatives?.[0]?.transcript ?? '';

    return { text: transcript, model: this.model || 'nvidia-asr', durationMs: Date.now() - startedAt };
  }
}

/** NVIDIA Riva / NIM text-to-speech over HTTP, returning base64 audio. */
export class NvidiaTextToSpeechProvider implements TextToSpeechProvider {
  readonly id = 'nvidia:tts';
  readonly mode: VoiceMode = 'server';

  constructor(
    private readonly url: string,
    private readonly model: string,
    private readonly voice: string,
    private readonly apiKey: string,
  ) {}

  private unavailableReason(): string | null {
    if (!this.url) return 'nvidia:tts: NVIDIA_TTS_URL is not configured';
    if (!this.apiKey) return 'nvidia:tts: NVIDIA_API_KEY is not configured';
    return null;
  }

  isAvailable(): boolean {
    return this.unavailableReason() === null;
  }

  status(): ProviderStatus {
    const reason = this.unavailableReason();
    const status: ProviderStatus = {
      id: this.id,
      kind: 'tts',
      available: reason === null,
      model: this.model,
      endpoint: safeHost(this.url),
    };
    if (reason) status.reason = reason;
    return status;
  }

  async synthesize(input: {
    text: string;
    voice?: string;
    signal?: AbortSignal;
  }): Promise<SynthesisResult> {
    const reason = this.unavailableReason();
    if (reason) throw new VoiceUnavailableError(this.id, reason);

    const response = await fetchWithTimeout(this.url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
        authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify({
        text: input.text,
        voice: input.voice || this.voice || undefined,
        ...(this.model ? { model: this.model } : {}),
        encoding: 'wav',
      }),
      timeoutMs: 120_000,
      ...(input.signal ? { signal: input.signal } : {}),
    });

    const body = await response.text();
    if (!response.ok) {
      throw new VoiceUnavailableError(this.id, `HTTP ${response.status}: ${truncate(body, 300)}`);
    }
    const payload = JSON.parse(body) as { audio?: string; audio_content?: string; b64?: string };
    const audioB64 = payload.audio ?? payload.audio_content ?? payload.b64;
    if (!audioB64) throw new VoiceUnavailableError(this.id, 'response did not contain audio data');

    return { audioB64, mimeType: 'audio/wav', model: this.model || 'nvidia-tts' };
  }
}

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

export function createSpeechToTextProvider(config: JarvisConfig): SpeechToTextProvider {
  if (config.stt.provider === 'nvidia') {
    return new NvidiaSpeechToTextProvider(config.stt.url, config.stt.model, config.nvidia.apiKey);
  }
  return new BrowserSpeechToTextProvider();
}

export function createTextToSpeechProvider(config: JarvisConfig): TextToSpeechProvider {
  if (config.tts.provider === 'nvidia') {
    return new NvidiaTextToSpeechProvider(
      config.tts.url,
      config.tts.model,
      config.tts.voice,
      config.nvidia.apiKey,
    );
  }
  return new BrowserTextToSpeechProvider();
}
