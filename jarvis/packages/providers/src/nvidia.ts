/**
 * NVIDIA model provider.
 *
 * Targets NVIDIA's OpenAI-compatible inference API — both the hosted catalogue
 * at integrate.api.nvidia.com and self-hosted NIM containers, which expose the
 * same `/v1/chat/completions` contract. The same class therefore covers the
 * "future local NVIDIA GPU" case: point NVIDIA_BASE_URL at the local NIM.
 */
import type { ModelEndpointConfig } from '@jarvis/shared';
import { OpenAICompatibleProvider } from './openai-compatible.ts';

export class NvidiaProvider extends OpenAICompatibleProvider {
  constructor(config: ModelEndpointConfig) {
    super({
      id: 'nvidia',
      apiKey: config.apiKey,
      baseUrl: config.baseUrl || 'https://integrate.api.nvidia.com/v1',
      model: config.model,
      // Self-hosted NIMs frequently run without auth; the hosted API always
      // needs a key, so we require one unless the endpoint is clearly local.
      requiresApiKey: !isLocalEndpoint(config.baseUrl),
    });
  }
}

export function isLocalEndpoint(baseUrl: string): boolean {
  try {
    const host = new URL(baseUrl).hostname;
    return (
      host === 'localhost' ||
      host === '127.0.0.1' ||
      host === '::1' ||
      host === '0.0.0.0' ||
      host.endsWith('.local')
    );
  } catch {
    return false;
  }
}
