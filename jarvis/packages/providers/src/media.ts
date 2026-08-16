/**
 * Image, image-editing, video and vision provider interfaces.
 *
 * These are real adapters, not placeholders with fabricated output. When the
 * matching NVIDIA endpoint is configured they call it; when it is not, they
 * report `available: false` with the reason and every call throws
 * ProviderUnavailableError. JARVIS never claims a capability it does not have.
 */
import type { JarvisConfig, ProviderStatus } from '@jarvis/shared';
import { fetchWithTimeout, safeHost, truncate } from '@jarvis/shared';
import { ProviderError, ProviderUnavailableError } from './types.ts';

export interface GeneratedImage {
  /** base64-encoded image bytes. */
  b64: string;
  mimeType: string;
  model: string;
  seed?: number;
}

export interface GeneratedVideo {
  /** base64-encoded video bytes, when the endpoint returns them inline. */
  b64?: string;
  /** URL returned by asynchronous video endpoints. */
  url?: string;
  model: string;
  status: 'complete' | 'pending';
}

export interface CapabilityProvider {
  readonly id: string;
  isAvailable(): boolean;
  status(): ProviderStatus;
}

export interface ImageGenerationProvider extends CapabilityProvider {
  generate(input: {
    prompt: string;
    negativePrompt?: string;
    width?: number;
    height?: number;
    steps?: number;
    seed?: number;
    signal?: AbortSignal;
  }): Promise<GeneratedImage>;
}

export interface ImageEditingProvider extends CapabilityProvider {
  edit(input: {
    prompt: string;
    /** base64-encoded source image. */
    imageB64: string;
    maskB64?: string;
    strength?: number;
    signal?: AbortSignal;
  }): Promise<GeneratedImage>;
}

export interface VideoGenerationProvider extends CapabilityProvider {
  generate(input: {
    prompt: string;
    imageB64?: string;
    durationSeconds?: number;
    seed?: number;
    signal?: AbortSignal;
  }): Promise<GeneratedVideo>;
}

export interface VisionProvider extends CapabilityProvider {
  describe(input: {
    imageB64: string;
    prompt?: string;
    signal?: AbortSignal;
  }): Promise<{ text: string; model: string }>;
}

// ---------------------------------------------------------------------------
// Unavailable implementations
// ---------------------------------------------------------------------------

class UnavailableCapability implements CapabilityProvider {
  constructor(
    readonly id: string,
    private readonly kind: ProviderStatus['kind'],
    private readonly reason: string,
  ) {}

  isAvailable(): boolean {
    return false;
  }

  status(): ProviderStatus {
    return { id: this.id, kind: this.kind, available: false, reason: this.reason };
  }

  protected fail(): never {
    throw new ProviderUnavailableError(this.id, this.reason);
  }
}

export class UnavailableImageProvider extends UnavailableCapability implements ImageGenerationProvider {
  constructor(reason: string) {
    super('image:none', 'image', reason);
  }
  async generate(): Promise<GeneratedImage> {
    return this.fail();
  }
}

export class UnavailableImageEditingProvider
  extends UnavailableCapability
  implements ImageEditingProvider
{
  constructor(reason: string) {
    super('image_edit:none', 'image_edit', reason);
  }
  async edit(): Promise<GeneratedImage> {
    return this.fail();
  }
}

export class UnavailableVideoProvider extends UnavailableCapability implements VideoGenerationProvider {
  constructor(reason: string) {
    super('video:none', 'video', reason);
  }
  async generate(): Promise<GeneratedVideo> {
    return this.fail();
  }
}

export class UnavailableVisionProvider extends UnavailableCapability implements VisionProvider {
  constructor(reason: string) {
    super('vision:none', 'vision', reason);
  }
  async describe(): Promise<{ text: string; model: string }> {
    return this.fail();
  }
}

// ---------------------------------------------------------------------------
// NVIDIA implementations
// ---------------------------------------------------------------------------

interface NvidiaMediaResponse {
  artifacts?: { base64?: string; seed?: number; finishReason?: string }[];
  image?: string;
  video?: string;
  b64_json?: string;
  data?: { b64_json?: string; url?: string }[];
  status?: string;
  error?: { message?: string };
  detail?: string;
}

async function postNvidia(
  providerId: string,
  url: string,
  apiKey: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<NvidiaMediaResponse> {
  let response: Response;
  try {
    response = await fetchWithTimeout(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
        ...(apiKey ? { authorization: `Bearer ${apiKey}` } : {}),
      },
      body: JSON.stringify(body),
      timeoutMs: 180_000,
      ...(signal ? { signal } : {}),
    });
  } catch (error) {
    throw new ProviderError(providerId, `request to ${safeHost(url)} failed: ${(error as Error).message}`, {
      retryable: true,
    });
  }

  const text = await response.text();
  if (!response.ok) {
    throw new ProviderError(providerId, `HTTP ${response.status}: ${truncate(text, 300)}`, {
      status: response.status,
    });
  }
  try {
    return JSON.parse(text) as NvidiaMediaResponse;
  } catch {
    throw new ProviderError(providerId, `invalid JSON response: ${truncate(text, 200)}`);
  }
}

function extractImage(payload: NvidiaMediaResponse, model: string, providerId: string): GeneratedImage {
  const b64 =
    payload.artifacts?.[0]?.base64 ?? payload.image ?? payload.b64_json ?? payload.data?.[0]?.b64_json;
  if (!b64) {
    throw new ProviderError(providerId, 'response did not contain image data');
  }
  const image: GeneratedImage = { b64, mimeType: 'image/png', model };
  const seed = payload.artifacts?.[0]?.seed;
  if (seed !== undefined) image.seed = seed;
  return image;
}

abstract class NvidiaMediaBase {
  constructor(
    readonly id: string,
    protected readonly url: string,
    protected readonly model: string,
    protected readonly apiKey: string,
    protected readonly kind: ProviderStatus['kind'],
  ) {}

  protected unavailableReason(): string | null {
    if (!this.url) return `${this.id}: endpoint URL is not configured`;
    if (!this.model) return `${this.id}: model is not configured`;
    if (!this.apiKey) return `${this.id}: NVIDIA_API_KEY is not configured`;
    return null;
  }

  isAvailable(): boolean {
    return this.unavailableReason() === null;
  }

  status(): ProviderStatus {
    const reason = this.unavailableReason();
    const status: ProviderStatus = {
      id: this.id,
      kind: this.kind,
      available: reason === null,
      model: this.model,
      endpoint: safeHost(this.url),
    };
    if (reason) status.reason = reason;
    return status;
  }

  protected assertAvailable(): void {
    const reason = this.unavailableReason();
    if (reason) throw new ProviderUnavailableError(this.id, reason);
  }
}

export class NvidiaImageProvider extends NvidiaMediaBase implements ImageGenerationProvider {
  constructor(config: { url: string; model: string; apiKey: string }) {
    super('nvidia:image', config.url, config.model, config.apiKey, 'image');
  }

  async generate(input: {
    prompt: string;
    negativePrompt?: string;
    width?: number;
    height?: number;
    steps?: number;
    seed?: number;
    signal?: AbortSignal;
  }): Promise<GeneratedImage> {
    this.assertAvailable();
    const body: Record<string, unknown> = {
      prompt: input.prompt,
      width: input.width ?? 1024,
      height: input.height ?? 1024,
      steps: input.steps ?? 30,
      seed: input.seed ?? 0,
    };
    if (input.negativePrompt) body.negative_prompt = input.negativePrompt;
    const payload = await postNvidia(this.id, this.url, this.apiKey, body, input.signal);
    return extractImage(payload, this.model, this.id);
  }
}

export class NvidiaImageEditingProvider extends NvidiaMediaBase implements ImageEditingProvider {
  constructor(config: { url: string; model: string; apiKey: string }) {
    super('nvidia:image_edit', config.url, config.model, config.apiKey, 'image_edit');
  }

  async edit(input: {
    prompt: string;
    imageB64: string;
    maskB64?: string;
    strength?: number;
    signal?: AbortSignal;
  }): Promise<GeneratedImage> {
    this.assertAvailable();
    const body: Record<string, unknown> = {
      prompt: input.prompt,
      image: input.imageB64,
      strength: input.strength ?? 0.6,
    };
    if (input.maskB64) body.mask = input.maskB64;
    const payload = await postNvidia(this.id, this.url, this.apiKey, body, input.signal);
    return extractImage(payload, this.model, this.id);
  }
}

export class NvidiaVideoProvider extends NvidiaMediaBase implements VideoGenerationProvider {
  constructor(config: { url: string; model: string; apiKey: string }) {
    super('nvidia:video', config.url, config.model, config.apiKey, 'video');
  }

  async generate(input: {
    prompt: string;
    imageB64?: string;
    durationSeconds?: number;
    seed?: number;
    signal?: AbortSignal;
  }): Promise<GeneratedVideo> {
    this.assertAvailable();
    const body: Record<string, unknown> = { prompt: input.prompt, seed: input.seed ?? 0 };
    if (input.imageB64) body.image = input.imageB64;
    if (input.durationSeconds) body.duration = input.durationSeconds;

    const payload = await postNvidia(this.id, this.url, this.apiKey, body, input.signal);
    const b64 = payload.video ?? payload.artifacts?.[0]?.base64;
    const url = payload.data?.[0]?.url;
    if (!b64 && !url) {
      throw new ProviderError(this.id, 'response did not contain video data or a result URL');
    }
    const video: GeneratedVideo = {
      model: this.model,
      status: payload.status === 'pending' ? 'pending' : 'complete',
    };
    if (b64) video.b64 = b64;
    if (url) video.url = url;
    return video;
  }
}

/** Vision goes through the NVIDIA chat endpoint using an image content part. */
export class NvidiaVisionProvider implements VisionProvider {
  readonly id = 'nvidia:vision';
  constructor(
    private readonly baseUrl: string,
    private readonly model: string,
    private readonly apiKey: string,
  ) {}

  private unavailableReason(): string | null {
    if (!this.model) return 'nvidia:vision: NVIDIA_VISION_MODEL is not configured';
    if (!this.apiKey) return 'nvidia:vision: NVIDIA_API_KEY is not configured';
    if (!this.baseUrl) return 'nvidia:vision: NVIDIA_BASE_URL is not configured';
    return null;
  }

  isAvailable(): boolean {
    return this.unavailableReason() === null;
  }

  status(): ProviderStatus {
    const reason = this.unavailableReason();
    const status: ProviderStatus = {
      id: this.id,
      kind: 'vision',
      available: reason === null,
      model: this.model,
      endpoint: safeHost(this.baseUrl),
    };
    if (reason) status.reason = reason;
    return status;
  }

  async describe(input: {
    imageB64: string;
    prompt?: string;
    signal?: AbortSignal;
  }): Promise<{ text: string; model: string }> {
    const reason = this.unavailableReason();
    if (reason) throw new ProviderUnavailableError(this.id, reason);

    const url = `${this.baseUrl.replace(/\/+$/, '')}/chat/completions`;
    const body = {
      model: this.model,
      max_tokens: 512,
      messages: [
        {
          role: 'user',
          content: [
            { type: 'text', text: input.prompt ?? 'Describe this image precisely and concisely.' },
            { type: 'image_url', image_url: { url: `data:image/png;base64,${input.imageB64}` } },
          ],
        },
      ],
    };

    const response = await fetchWithTimeout(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify(body),
      timeoutMs: 120_000,
      ...(input.signal ? { signal: input.signal } : {}),
    });

    const text = await response.text();
    if (!response.ok) {
      throw new ProviderError(this.id, `HTTP ${response.status}: ${truncate(text, 300)}`, {
        status: response.status,
      });
    }
    const parsed = JSON.parse(text) as {
      choices?: { message?: { content?: string } }[];
      model?: string;
    };
    return {
      text: parsed.choices?.[0]?.message?.content ?? '',
      model: parsed.model ?? this.model,
    };
  }
}

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

export function createImageProvider(config: JarvisConfig): ImageGenerationProvider {
  if (config.image.provider !== 'nvidia') {
    return new UnavailableImageProvider(
      'Image generation is not configured. Set IMAGE_PROVIDER=nvidia with NVIDIA_IMAGE_URL and NVIDIA_IMAGE_MODEL.',
    );
  }
  return new NvidiaImageProvider({
    url: config.image.url,
    model: config.image.model,
    apiKey: config.nvidia.apiKey,
  });
}

export function createImageEditingProvider(config: JarvisConfig): ImageEditingProvider {
  if (config.imageEdit.provider !== 'nvidia') {
    return new UnavailableImageEditingProvider(
      'Image editing is not configured. Set IMAGE_EDIT_PROVIDER=nvidia with NVIDIA_IMAGE_EDIT_URL and NVIDIA_IMAGE_EDIT_MODEL.',
    );
  }
  return new NvidiaImageEditingProvider({
    url: config.imageEdit.url,
    model: config.imageEdit.model,
    apiKey: config.nvidia.apiKey,
  });
}

export function createVideoProvider(config: JarvisConfig): VideoGenerationProvider {
  if (config.video.provider !== 'nvidia') {
    return new UnavailableVideoProvider(
      'Video generation is not configured. Set VIDEO_PROVIDER=nvidia with NVIDIA_VIDEO_URL and NVIDIA_VIDEO_MODEL.',
    );
  }
  return new NvidiaVideoProvider({
    url: config.video.url,
    model: config.video.model,
    apiKey: config.nvidia.apiKey,
  });
}

export function createVisionProvider(config: JarvisConfig): VisionProvider {
  if (config.vision.provider !== 'nvidia') {
    return new UnavailableVisionProvider(
      'Vision is not configured. Set VISION_PROVIDER=nvidia with NVIDIA_VISION_MODEL.',
    );
  }
  return new NvidiaVisionProvider(config.nvidia.baseUrl, config.vision.model, config.nvidia.apiKey);
}
