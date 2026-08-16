/**
 * Capability-based model routing.
 *
 * The orchestrator asks for *capabilities* — "I need tool calling", "I need
 * vision" — and the registry answers with a provider that has them. It never
 * asks for a vendor, and nothing here inspects the user's message.
 *
 * Explicitly **not** difficulty routing. Deciding "this question is hard" before
 * answering it means either a second model call (paying latency and cost to
 * save latency and cost) or a heuristic that is wrong in both directions, and a
 * wrong route is worse than no route. Capabilities are facts about a model;
 * difficulty is a guess about a request. This routes on the facts.
 *
 * Every decision is deterministic and explainable: the preference order is
 * configuration, the first provider satisfying the requirement wins, and the
 * reason is recorded alongside the result.
 */
import type { JarvisConfig, ModelProviderId } from '@jarvis/shared';
import type { ModelProvider } from './types.ts';
import { createAllModelProviders } from './registry.ts';

/**
 * What a model can do.
 *
 * These are properties of the configured *model*, not the vendor — the same
 * provider serves vision and non-vision models — so they are configured per
 * provider rather than hard-coded per vendor name.
 */
export interface ModelCapability {
  /** Native function/tool calling. */
  toolUse: boolean;
  /** Image inputs. */
  vision: boolean;
  /** Reliable JSON/structured output. */
  structuredOutput: boolean;
  /** Streaming responses. Declared for completeness; unused until voice streaming. */
  streaming: boolean;
  /** Suited to code work. */
  coding: boolean;
  /** Long-context window (roughly, ≥100k tokens). */
  longContext: boolean;
}

export type CapabilityName = keyof ModelCapability;

export const CAPABILITY_NAMES: CapabilityName[] = [
  'toolUse',
  'vision',
  'structuredOutput',
  'streaming',
  'coding',
  'longContext',
];

/**
 * Defaults per provider.
 *
 * Deliberately conservative: a capability defaults to false unless the provider
 * class generally has it. Operators override per deployment via
 * `<PROVIDER>_CAPABILITIES`, because the truth depends on which model is
 * configured, and JARVIS cannot inspect a remote model to find out.
 */
export const DEFAULT_CAPABILITIES: Record<ModelProviderId, ModelCapability> = {
  nvidia: {
    toolUse: true,
    vision: false,
    structuredOutput: true,
    streaming: true,
    coding: true,
    longContext: false,
  },
  anthropic: {
    toolUse: true,
    vision: true,
    structuredOutput: true,
    streaming: true,
    coding: true,
    longContext: true,
  },
  openai: {
    toolUse: true,
    vision: true,
    structuredOutput: true,
    streaming: true,
    coding: true,
    longContext: true,
  },
  local: {
    // A local runtime could be anything; claim nothing until told otherwise.
    toolUse: false,
    vision: false,
    structuredOutput: false,
    streaming: false,
    coding: false,
    longContext: false,
  },
};

export interface RoutingDecision {
  provider: ModelProvider;
  providerId: ModelProviderId;
  model: string;
  /** Capabilities the caller required. */
  requested: CapabilityName[];
  /** True when the configured default could not serve the request. */
  fallbackUsed: boolean;
  /** Human-readable justification. Recorded with the usage row. */
  reason: string;
}

export class NoCapableModelError extends Error {
  readonly code = 'no_capable_model';
  readonly requested: CapabilityName[];
  readonly considered: { providerId: string; missing: CapabilityName[]; available: boolean }[];

  constructor(
    requested: CapabilityName[],
    considered: { providerId: string; missing: CapabilityName[]; available: boolean }[],
  ) {
    const detail = considered
      .map((entry) =>
        entry.available
          ? `${entry.providerId} lacks ${entry.missing.join(', ')}`
          : `${entry.providerId} is not configured`,
      )
      .join('; ');
    super(
      `no configured model provides ${requested.join(' + ')}. Considered: ${detail || 'nothing'}.`,
    );
    this.name = 'NoCapableModelError';
    this.requested = requested;
    this.considered = considered;
  }
}

/** Parse `toolUse,vision` into a capability set, ignoring unknown names. */
export function parseCapabilities(raw: string, base: ModelCapability): ModelCapability {
  if (!raw.trim()) return { ...base };
  const declared = new Set(
    raw
      .split(',')
      .map((entry) => entry.trim())
      .filter(Boolean),
  );
  const result = { ...base };
  for (const name of CAPABILITY_NAMES) result[name] = declared.has(name);
  return result;
}

export interface ModelRouterOptions {
  /** Overrides the registry — used by tests and by a single-provider setup. */
  providers?: Partial<Record<ModelProviderId, ModelProvider>>;
  capabilities?: Partial<Record<ModelProviderId, ModelCapability>>;
}

/**
 * Selects a provider by capability.
 *
 * Preference order: the configured default first, then the explicit fallback
 * order. That makes routing predictable — a request needing nothing special
 * always lands on the configured model, and a request needing something the
 * default lacks moves in a fixed, declared order rather than by search.
 */
export class ModelRouter {
  private readonly providers: Record<ModelProviderId, ModelProvider>;
  private readonly capabilities: Record<ModelProviderId, ModelCapability>;
  private readonly defaultProvider: ModelProviderId;
  private readonly fallbackOrder: ModelProviderId[];

  constructor(config: JarvisConfig, options: ModelRouterOptions = {}) {
    const built = createAllModelProviders(config);
    this.providers = { ...built, ...(options.providers as Record<ModelProviderId, ModelProvider>) };
    this.capabilities = {
      nvidia: options.capabilities?.nvidia ?? config.capabilities.nvidia,
      anthropic: options.capabilities?.anthropic ?? config.capabilities.anthropic,
      openai: options.capabilities?.openai ?? config.capabilities.openai,
      local: options.capabilities?.local ?? config.capabilities.local,
    };
    this.defaultProvider = config.modelProvider;
    this.fallbackOrder = config.routingFallback;
  }

  capabilitiesOf(providerId: ModelProviderId): ModelCapability {
    return this.capabilities[providerId];
  }

  /** Providers in the order they will be considered, without duplicates. */
  private order(): ModelProviderId[] {
    const seen = new Set<ModelProviderId>();
    const ordered: ModelProviderId[] = [];
    for (const providerId of [this.defaultProvider, ...this.fallbackOrder]) {
      if (!seen.has(providerId)) {
        seen.add(providerId);
        ordered.push(providerId);
      }
    }
    return ordered;
  }

  private missing(providerId: ModelProviderId, requested: CapabilityName[]): CapabilityName[] {
    const capability = this.capabilities[providerId];
    return requested.filter((name) => !capability[name]);
  }

  /**
   * Choose a model.
   *
   * Throws `NoCapableModelError` rather than quietly downgrading: sending a
   * vision request to a model that cannot see produces a confidently wrong
   * answer, which is worse than a clear failure.
   */
  select(requested: CapabilityName[] = []): RoutingDecision {
    const considered: { providerId: string; missing: CapabilityName[]; available: boolean }[] = [];

    for (const [index, providerId] of this.order().entries()) {
      const provider = this.providers[providerId];
      const available = provider?.isAvailable() ?? false;
      const missing = this.missing(providerId, requested);

      considered.push({ providerId, missing, available });
      if (!provider || !available || missing.length > 0) continue;

      const isDefault = index === 0;
      return {
        provider,
        providerId,
        model: provider.model,
        requested,
        fallbackUsed: !isDefault,
        reason: isDefault
          ? requested.length === 0
            ? `default provider ${providerId}`
            : `default provider ${providerId} provides ${requested.join(' + ')}`
          : `${this.defaultProvider} could not serve ${requested.join(' + ') || 'the request'}; ` +
            `fell back to ${providerId} in declared order`,
      };
    }

    throw new NoCapableModelError(requested, considered);
  }

  /** What each provider offers. Used by the capability view. */
  inventory(): {
    providerId: ModelProviderId;
    model: string;
    available: boolean;
    reason?: string;
    capabilities: ModelCapability;
    isDefault: boolean;
  }[] {
    return this.order().map((providerId) => {
      const provider = this.providers[providerId];
      const status = provider.status();
      const entry: {
        providerId: ModelProviderId;
        model: string;
        available: boolean;
        reason?: string;
        capabilities: ModelCapability;
        isDefault: boolean;
      } = {
        providerId,
        model: provider.model,
        available: provider.isAvailable(),
        capabilities: this.capabilities[providerId],
        isDefault: providerId === this.defaultProvider,
      };
      if (status.reason) entry.reason = status.reason;
      return entry;
    });
  }
}
