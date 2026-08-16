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
  type ApprovalRequest,
  type JarvisConfig,
  type JarvisEvent,
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
import { RuntimeMonitor, type RuntimeActivity } from './monitor.ts';
import { TelemetryCollector, type SystemTelemetry } from './telemetry.ts';

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

/**
 * One consistent snapshot of everything a control surface needs.
 *
 * Composed from systems that already exist — the monitor, the store, the
 * registry, the provider registry — so a client can render live state without
 * six round-trips that disagree with each other. It adds no logic of its own.
 */
export interface RuntimeStateSnapshot {
  version: string;
  sampledAt: string;
  activity: RuntimeActivity;
  telemetry: SystemTelemetry;
  providers: ProviderStatus[];
  activeModel: { provider: string; model: string; available: boolean; reason?: string };
  voice: {
    stt: ProviderStatus & { mode: string };
    tts: ProviderStatus & { mode: string };
  };
  media: { image: ProviderStatus; imageEdit: ProviderStatus; video: ProviderStatus; vision: ProviderStatus };
  agents: {
    name: string;
    title: string;
    purpose: string;
    maxRisk: string;
    readOnly: boolean;
    maxIterations: number;
    tools: string[];
    runCount: number;
    lastRunAt: string | null;
    running: boolean;
  }[];
  approvals: ApprovalRequest[];
  tools: { total: number; requiringApproval: number; names: string[] };
  database: { ok: boolean; schemaVersion: number; tables: string[] };
  counts: { memories: number; auditEvents: number; pendingApprovals: number; conversations: number };
  recentEvents: JarvisEvent[];
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
  readonly monitor: RuntimeMonitor;
  readonly telemetry: TelemetryCollector;
  private approvalSweep: ReturnType<typeof setInterval> | null = null;

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

    // Live runtime state and telemetry. The monitor is a pure subscriber: it
    // observes the bus and never calls back into execution.
    this.monitor = new RuntimeMonitor(this.events);
    this.telemetry = new TelemetryCollector();

    // Expiry can be triggered by any approval read, so the announcement is
    // attached to the repository rather than to a periodic sweep.
    this.store.approvals.onExpire = (expired) => this.announceExpiredApprovals(expired);

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

  /**
   * Expire timed-out approvals and announce each one.
   *
   * Expiry used to happen silently inside a SQL UPDATE, so a request could time
   * out with nothing observable: no event, no audit row, and a card left on
   * screen offering to approve something that could no longer run. Every
   * expiry now produces the same evidence a human decision does.
   */
  sweepExpiredApprovals(): ApprovalRequest[] {
    return this.store.approvals.expireStale();
  }

  /**
   * Announce expired approvals.
   *
   * Wired to the repository's hook rather than called from a sweep: expiry is
   * triggered by ordinary reads (`listPending`, `get`, `resolve`), so a sweep
   * would almost always find the rows already quietly expired.
   */
  private announceExpiredApprovals(expired: ApprovalRequest[]): void {
    for (const approval of expired) {
      this.store.audit.record({
        userId: approval.userId,
        agent: approval.agent,
        tool: approval.tool,
        arguments: approval.arguments,
        approvalState: 'expired',
        approvalId: approval.id,
        error: 'approval expired before a decision was made',
        durationMs: 0,
        risk: approval.risk,
        conversationId: approval.conversationId,
      });
      this.events.emit({
        type: 'APPROVAL_RESOLVED',
        userId: approval.userId,
        conversationId: approval.conversationId,
        agent: approval.agent,
        summary: `Expired: ${approval.description}`,
        data: {
          approvalId: approval.id,
          tool: approval.tool,
          risk: approval.risk,
          decision: 'expired',
        },
      });
    }
  }

  /**
   * Start periodic background work.
   *
   * Only the server calls this. Tests construct a Jarvis without timers, so a
   * suite never leaves a handle open or races a sweep.
   */
  startBackgroundTasks(intervalMs = 30_000): void {
    if (this.approvalSweep) return;
    this.approvalSweep = setInterval(() => {
      try {
        this.sweepExpiredApprovals();
      } catch {
        /* a failed sweep must not take the process down */
      }
    }, intervalMs);
    // Never hold the event loop open on account of housekeeping.
    this.approvalSweep.unref?.();
  }

  stopBackgroundTasks(): void {
    if (this.approvalSweep) clearInterval(this.approvalSweep);
    this.approvalSweep = null;
  }

  /**
   * One consistent snapshot of live runtime state.
   *
   * Pure composition of existing systems — no logic lives here that is not
   * already the runtime's answer to the same question.
   */
  runtimeState(userId: string): RuntimeStateSnapshot {
    this.sweepExpiredApprovals();

    const approvals = this.store.approvals.listPending(userId);
    this.monitor.syncPendingApprovals(approvals.length);

    const activity = this.monitor.snapshot();
    const runningAgents = new Set(activity.activeAgents.map((run) => run.agent));
    const records = this.store.agents.list();
    const providerStatus = this.provider.status();

    return {
      version: JARVIS_VERSION,
      sampledAt: new Date().toISOString(),
      activity,
      telemetry: this.telemetry.sample(),
      providers: [
        ...modelProviderStatuses(this.config),
        this.stt.status(),
        this.tts.status(),
        this.image.status(),
        this.imageEdit.status(),
        this.video.status(),
        this.vision.status(),
        this.search.status(),
      ],
      activeModel: {
        provider: this.provider.id,
        model: this.provider.model,
        available: providerStatus.available,
        ...(providerStatus.reason ? { reason: providerStatus.reason } : {}),
      },
      voice: {
        stt: { ...this.stt.status(), mode: this.stt.mode },
        tts: { ...this.tts.status(), mode: this.tts.mode },
      },
      media: {
        image: this.image.status(),
        imageEdit: this.imageEdit.status(),
        video: this.video.status(),
        vision: this.vision.status(),
      },
      agents: Object.values(this.agents).map((definition) => {
        const record = records.find((row) => row.name === definition.name);
        return {
          name: definition.name,
          title: definition.title,
          purpose: definition.purpose,
          maxRisk: definition.maxRisk,
          readOnly: definition.readOnly,
          maxIterations: definition.maxIterations,
          tools: this.registry.listForAgent(definition).map((tool) => tool.name),
          runCount: record?.runCount ?? 0,
          lastRunAt: record?.lastRunAt ?? null,
          running: runningAgents.has(definition.name),
        };
      }),
      approvals,
      tools: {
        total: this.registry.size,
        requiringApproval: this.registry.list().filter((tool) => this.policy.approvalFor(tool).required)
          .length,
        names: this.registry.list().map((tool) => tool.name),
      },
      database: this.store.health(),
      counts: {
        memories: this.store.memories.count(userId),
        auditEvents: this.store.audit.count(userId),
        pendingApprovals: approvals.length,
        conversations: this.store.conversations.list(userId, 500).length,
      },
      recentEvents: this.store.events.list(userId, { limit: 50 }),
      charterErrors: this.charterErrors,
    };
  }

  publicConfig() {
    return publicConfig(this.config);
  }

  close(): void {
    this.stopBackgroundTasks();
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
