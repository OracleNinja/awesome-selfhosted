import type { JarvisConfig, ModelProviderId, ProviderStatus } from '@jarvis/shared';
import { OpenAICompatibleProvider } from './openai-compatible.ts';
import { NvidiaProvider } from './nvidia.ts';
import { AnthropicProvider } from './anthropic.ts';
import type { ModelProvider } from './types.ts';

/** OpenAI proper (and any gateway that mimics it exactly). */
export class OpenAIProvider extends OpenAICompatibleProvider {
  constructor(config: { apiKey: string; baseUrl: string; model: string }) {
    super({
      id: 'openai',
      apiKey: config.apiKey,
      baseUrl: config.baseUrl || 'https://api.openai.com/v1',
      model: config.model,
      requiresApiKey: true,
    });
  }
}

/**
 * Local inference placeholder.
 *
 * Not a stub: it is a working OpenAI-compatible client with no default
 * endpoint. Point LOCAL_BASE_URL at vLLM / llama.cpp / Ollama / a local NIM and
 * it becomes available. Until then it reports itself unavailable and explains why.
 */
export class LocalProvider extends OpenAICompatibleProvider {
  constructor(config: { apiKey: string; baseUrl: string; model: string }) {
    super({
      id: 'local',
      apiKey: config.apiKey,
      baseUrl: config.baseUrl,
      model: config.model,
      requiresApiKey: false,
    });
  }
}

export const MODEL_PROVIDER_IDS: ModelProviderId[] = ['nvidia', 'anthropic', 'openai', 'local'];

export function createModelProvider(config: JarvisConfig, which?: ModelProviderId): ModelProvider {
  const target = which ?? config.modelProvider;
  switch (target) {
    case 'nvidia':
      return new NvidiaProvider(config.nvidia);
    case 'anthropic':
      return new AnthropicProvider(config.anthropic);
    case 'openai':
      return new OpenAIProvider(config.openai);
    case 'local':
      return new LocalProvider(config.local);
  }
}

/** Every model provider, whether configured or not — used by the status view. */
export function createAllModelProviders(config: JarvisConfig): Record<ModelProviderId, ModelProvider> {
  return {
    nvidia: createModelProvider(config, 'nvidia'),
    anthropic: createModelProvider(config, 'anthropic'),
    openai: createModelProvider(config, 'openai'),
    local: createModelProvider(config, 'local'),
  };
}

export function modelProviderStatuses(config: JarvisConfig): ProviderStatus[] {
  const providers = createAllModelProviders(config);
  return MODEL_PROVIDER_IDS.map((providerId) => providers[providerId].status());
}
