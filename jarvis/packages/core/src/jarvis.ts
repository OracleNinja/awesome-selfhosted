/**
 * Composition root.
 *
 * Every dependency is constructed here, once, and passed down explicitly.
 * There is no global state, no service locator and no module-level singleton —
 * which is why the tests can stand up a complete JARVIS against an in-memory
 * database and a fake provider in three lines.
 */
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import {
  EventBus,
  JARVIS_VERSION,
  publicConfig,
  repoRoot,
  resolveDatabasePath,
  truncate,
  type AgentDefinition,
  type JarvisConfig,
  type ProviderStatus,
} from '@jarvis/shared';
import { Store } from '@jarvis/memory';
import { PermissionPolicy } from '@jarvis/security';
import {
  createImageEditingProvider,
  createImageProvider,
  createModelProvider,
  createSearchProvider,
  createVideoProvider,
  createVisionProvider,
  modelProviderStatuses,
  type ImageEditingProvider,
  type ImageGenerationProvider,
  type ModelProvider,
  type SearchProvider,
  type VideoGenerationProvider,
  type VisionProvider,
} from '@jarvis/providers';
import {
  createSpeechToTextProvider,
  createTextToSpeechProvider,
  type SpeechToTextProvider,
  type TextToSpeechProvider,
} from '@jarvis/voice';
import { ToolRegistry, createBuiltinTools } from '@jarvis/tools';
import {
  AgentRunner,
  JARVIS_AGENT,
  delegateAgentTool,
  loadAgentDefinitions,
} from '@jarvis/agents';
import { ToolExecutor } from './executor.ts';
import { Orchestrator, type TurnResult } from './orchestrator.ts';

export interface JarvisSystemStatus {
  version: string;
  ok: boolean;
  activeModelProvider: string;
  activeModel: string;
  providers: ProviderStatus[];
  database: { ok: boolean; schemaVersion: number; tables: string[] };
  tools: { total: number; requiringApproval: number };
  agents: string[];
  charterErrors: string[];
}

export interface ApprovalOutcome {
  ok: boolean;
  state: 'approved' | 'denied' | 'unavailable';
  message: string;
  turn?: TurnResult;
}

export class Jarvis {
  readonly config: JarvisConfig;
  readonly store: Store;
  readonly events: EventBus;
  readonly policy: PermissionPolicy;
  readonly registry: ToolRegistry;
  readonly provider: ModelProvider;
  readonly executor: ToolExecutor;
  readonly orchestrator: Orchestrator;
  readonly runner: AgentRunner;
  readonly agents: Record<string, AgentDefinition>;
  readonly jarvisAgent: AgentDefinition;
  readonly search: SearchProvider;
  readonly stt: SpeechToTextProvider;
  readonly tts: TextToSpeechProvider;
  readonly image: ImageGenerationProvider;
  readonly imageEdit: ImageEditingProvider;
  readonly video: VideoGenerationProvider;
  readonly vision: VisionProvider;
  readonly charterErrors: string[];

  constructor(options: {
    config: JarvisConfig;
    /** Override the model provider — used by tests and by future per-agent routing. */
    provider?: ModelProvider;
    databasePath?: string;
    agentsDir?: string;
  }) {
    const { config } = options;
    this.config = config;

    this.store = new Store(options.databasePath ?? resolveDatabasePath(config.databaseUrl));
    this.events = new EventBus();
    this.policy = new PermissionPolicy(config.approvalRequiredLevels);
    this.registry = new ToolRegistry(this.policy);

    this.provider = options.provider ?? createModelProvider(config);
    this.search = createSearchProvider(config);
    this.stt = createSpeechToTextProvider(config);
    this.tts = createTextToSpeechProvider(config);
    this.image = createImageProvider(config);
    this.imageEdit = createImageEditingProvider(config);
    this.video = createVideoProvider(config);
    this.vision = createVisionProvider(config);

    // Persist the event stream so activity survives a restart.
    this.events.subscribe((event) => {
      try {
        this.store.events.record(event);
      } catch {
        /* never let logging break the request path */
      }
    });

    const charters = loadAgentDefinitions(options.agentsDir ?? join(repoRoot(), 'agents'));
    this.agents = charters.definitions;
    this.charterErrors = charters.errors;
    this.jarvisAgent = JARVIS_AGENT;

    this.executor = new ToolExecutor({
      registry: this.registry,
      policy: this.policy,
      store: this.store,
      events: this.events,
      approvalTimeoutSeconds: config.approvalTimeoutSeconds,
    });

    this.runner = new AgentRunner({
      provider: this.provider,
      registry: this.registry,
      executor: this.executor,
      events: this.events,
      onAgentRun: (name) => this.store.agents.recordRun(name),
    });

    this.registry.registerAll(
      createBuiltinTools({ store: this.store, config, search: this.search }),
    );
    this.registry.register(
      delegateAgentTool({
        runner: this.runner,
        definitions: this.agents,
        contextFor: (userId, task) => this.orchestrator.retrieveContext(userId, task, 4),
      }),
    );

    this.orchestrator = new Orchestrator({
      provider: this.provider,
      registry: this.registry,
      executor: this.executor,
      store: this.store,
      events: this.events,
      jarvisAgent: this.jarvisAgent,
    });

    for (const definition of Object.values(this.agents)) {
      this.store.agents.register(definition);
    }
    this.store.agents.register(this.jarvisAgent);

    mkdirSync(config.workspaceDir, { recursive: true });
  }

  /** Approve a pending request and execute it. */
  async approve(approvalId: string, userId: string, note?: string): Promise<ApprovalOutcome> {
    const existing = this.store.approvals.get(approvalId);
    if (!existing || existing.userId !== userId) {
      return { ok: false, state: 'unavailable', message: `Approval ${approvalId} was not found.` };
    }

    const resolved = this.store.approvals.resolve(approvalId, 'approved', userId, note);
    if (!resolved) {
      const current = this.store.approvals.get(approvalId);
      return {
        ok: false,
        state: 'unavailable',
        message: `Approval ${approvalId} is ${current?.state ?? 'unknown'} and cannot be approved again.`,
      };
    }

    this.events.emit({
      type: 'APPROVAL_RESOLVED',
      userId,
      conversationId: resolved.conversationId,
      agent: resolved.agent,
      summary: `Approved: ${resolved.description}`,
      data: { approvalId, tool: resolved.tool, risk: resolved.risk, decision: 'approved' },
    });

    const agent = this.agents[resolved.agent] ?? this.jarvisAgent;
    const outcome = await this.executor.executeApproved(resolved, agent);

    const noteText =
      outcome.status === 'executed'
        ? `The user APPROVED "${resolved.description}". It has now been executed. Result: ${truncate(
            outcome.result?.summary ?? 'completed',
            400,
          )}. Report this outcome to the user.`
        : `The user APPROVED "${resolved.description}", but execution did not succeed: ${truncate(
            outcome.message,
            400,
          )}. Report the failure accurately.`;

    const result: ApprovalOutcome = {
      ok: outcome.status === 'executed',
      state: 'approved',
      message: outcome.status === 'executed' ? (outcome.result?.summary ?? 'Executed.') : outcome.message,
    };

    if (resolved.conversationId) {
      result.turn = await this.orchestrator.continueAfterApproval({
        userId,
        conversationId: resolved.conversationId,
        note: noteText,
        fallback:
          outcome.status === 'executed'
            ? `Approved and executed: ${outcome.result?.summary ?? resolved.description}`
            : `Approved, but execution failed: ${outcome.message}`,
      });
    }
    return result;
  }

  /** Deny a pending request. The action is never executed. */
  async deny(approvalId: string, userId: string, note?: string): Promise<ApprovalOutcome> {
    const existing = this.store.approvals.get(approvalId);
    if (!existing || existing.userId !== userId) {
      return { ok: false, state: 'unavailable', message: `Approval ${approvalId} was not found.` };
    }

    const resolved = this.store.approvals.resolve(approvalId, 'denied', userId, note);
    if (!resolved) {
      const current = this.store.approvals.get(approvalId);
      return {
        ok: false,
        state: 'unavailable',
        message: `Approval ${approvalId} is ${current?.state ?? 'unknown'} and cannot be denied.`,
      };
    }

    this.store.audit.record({
      userId,
      agent: resolved.agent,
      tool: resolved.tool,
      arguments: resolved.arguments,
      approvalState: 'denied',
      approvalId,
      error: note ? `denied by user: ${note}` : 'denied by user',
      durationMs: 0,
      risk: resolved.risk,
      conversationId: resolved.conversationId,
    });

    this.events.emit({
      type: 'APPROVAL_RESOLVED',
      userId,
      conversationId: resolved.conversationId,
      agent: resolved.agent,
      summary: `Denied: ${resolved.description}`,
      data: { approvalId, tool: resolved.tool, risk: resolved.risk, decision: 'denied' },
    });

    const result: ApprovalOutcome = {
      ok: true,
      state: 'denied',
      message: `Denied. "${resolved.description}" was not executed.`,
    };

    if (resolved.conversationId) {
      result.turn = await this.orchestrator.continueAfterApproval({
        userId,
        conversationId: resolved.conversationId,
        note:
          `The user DENIED "${resolved.description}"${note ? ` with the note: ${note}` : ''}. ` +
          `It was NOT executed and must not be retried. Acknowledge briefly and offer an alternative if there is a sensible one.`,
        fallback: `Denied. "${resolved.description}" was not executed.`,
      });
    }
    return result;
  }

  status(): JarvisSystemStatus {
    const database = this.store.health();
    const providers: ProviderStatus[] = [
      ...modelProviderStatuses(this.config),
      this.stt.status(),
      this.tts.status(),
      this.image.status(),
      this.imageEdit.status(),
      this.video.status(),
      this.vision.status(),
      this.search.status(),
    ];

    return {
      version: JARVIS_VERSION,
      ok: database.ok,
      activeModelProvider: this.provider.id,
      activeModel: this.provider.model,
      providers,
      database,
      tools: {
        total: this.registry.size,
        requiringApproval: this.registry.list().filter((tool) => this.policy.approvalFor(tool).required)
          .length,
      },
      agents: Object.keys(this.agents),
      charterErrors: this.charterErrors,
    };
  }

  publicConfig() {
    return publicConfig(this.config);
  }

  close(): void {
    this.store.close();
  }
}

export function createJarvis(options: {
  config: JarvisConfig;
  provider?: ModelProvider;
  databasePath?: string;
  agentsDir?: string;
}): Jarvis {
  return new Jarvis(options);
}
