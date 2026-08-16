/**
 * Frontend runtime types.
 *
 * The domain types are imported from `@jarvis/shared/types`, not redeclared —
 * that module has no node imports, and type-only imports erase at build time,
 * so the browser bundle gains nothing at runtime while the compiler holds the
 * client and the runtime to the same shapes. If a backend type changes, this
 * app stops compiling, which is the point.
 *
 * Only two things are declared locally: the connection state (a property of the
 * link, which the runtime cannot know) and voice phase (see the note below).
 */
import type {
  ApprovalRequest,
  EventType,
  JarvisEvent,
  Memory,
  MemorySearchResult,
  MessageRole,
  ProviderStatus,
  RiskLevel,
  StoredMessage,
  ToolInfo,
} from '@jarvis/shared/types';

export type {
  ApprovalRequest,
  EventType,
  JarvisEvent,
  Memory,
  MemorySearchResult,
  MessageRole,
  ProviderStatus,
  RiskLevel,
  StoredMessage,
  ToolInfo,
};

// ---------------------------------------------------------------------------
// Mirrors of runtime-composed shapes (packages/core: monitor, telemetry, jarvis)
// ---------------------------------------------------------------------------

export type OrchestrationPhase =
  | 'idle'
  | 'thinking'
  | 'tool'
  | 'delegating'
  | 'awaiting_approval'
  | 'error';

export interface ActiveToolExecution {
  callId: string;
  tool: string;
  agent: string;
  risk: RiskLevel;
  startedAt: string;
  conversationId: string | null;
}

export interface ActiveAgentRun {
  agent: string;
  summary: string;
  startedAt: string;
  conversationId: string | null;
}

export interface RuntimeCounters {
  events: number;
  userMessages: number;
  modelResponses: number;
  toolCalls: number;
  toolFailures: number;
  agentRuns: number;
  approvalsRequested: number;
  approvalsResolved: number;
  memoryWrites: number;
  memoryReads: number;
  errors: number;
}

export interface RuntimeErrorInfo {
  summary: string;
  agent: string;
  kind: 'provider' | 'tool' | 'agent' | 'permission' | 'timeout' | 'unknown';
  at: string;
}

export interface RuntimeActivity {
  phase: OrchestrationPhase;
  busy: boolean;
  activeAgents: ActiveAgentRun[];
  activeTools: ActiveToolExecution[];
  lastError: RuntimeErrorInfo | null;
  counters: RuntimeCounters;
  lastEventAt: string | null;
  load: number;
}

export interface SystemTelemetry {
  uptimeSeconds: number;
  startedAt: string;
  sampledAt: string;
  nodeVersion: string;
  platform: string;
  hostname: string;
  pid: number;
  memory: {
    processRssBytes: number;
    processHeapUsedBytes: number;
    processHeapTotalBytes: number;
    systemTotalBytes: number | null;
    systemFreeBytes: number | null;
    systemUsedFraction: number | null;
  };
  cpu: {
    processCpuSeconds: number;
    processCpuFraction: number | null;
    cores: number;
    loadAverage1m: number | null;
    loadPerCore: number | null;
  };
}

export interface RuntimeAgentState {
  name: string;
  title: string;
  purpose: string;
  maxRisk: RiskLevel;
  readOnly: boolean;
  maxIterations: number;
  tools: string[];
  runCount: number;
  lastRunAt: string | null;
  running: boolean;
}

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
  media: {
    image: ProviderStatus;
    imageEdit: ProviderStatus;
    video: ProviderStatus;
    vision: ProviderStatus;
  };
  agents: RuntimeAgentState[];
  approvals: ApprovalRequest[];
  tools: { total: number; requiringApproval: number; names: string[] };
  database: { ok: boolean; schemaVersion: number; tables: string[] };
  counts: { memories: number; auditEvents: number; pendingApprovals: number; conversations: number };
  recentEvents: JarvisEvent[];
  charterErrors: string[];
}

// ---------------------------------------------------------------------------
// Client-local state
// ---------------------------------------------------------------------------

/** Health of the link to the runtime. Only the client can observe this. */
export type ConnectionState = 'connecting' | 'online' | 'degraded' | 'offline' | 'unauthorized';

/**
 * Voice phase.
 *
 * Deliberately client-owned. In `browser` mode capture, recognition and
 * synthesis all happen in this tab via the Web Speech API — the runtime is not
 * involved and genuinely cannot report whether a microphone is open. In
 * `server` mode the phase brackets the real `/api/voice/*` request.
 *
 * `source` is carried alongside so the UI can say which is true, rather than
 * implying the runtime is listening when it is the browser.
 */
export type VoicePhase = 'idle' | 'listening' | 'processing' | 'speaking' | 'error';

export interface VoiceState {
  phase: VoicePhase;
  /** Where the work happens, from the runtime's own provider status. */
  source: 'browser' | 'server';
  sttMode: string;
  ttsMode: string;
  sttAvailable: boolean;
  ttsAvailable: boolean;
  /** Whether this browser can actually do it, when source is `browser`. */
  supported: boolean;
  transcript: string;
  error: string | null;
}

/** A normalised event: the backend event plus a semantic label for the UI. */
export interface RuntimeEvent {
  id: string;
  type: EventType;
  /** The turn this event belongs to. Null for records written before v0.2. */
  turnId: string | null;
  /** Semantic name from the integration brief, derived — never sent by the backend. */
  kind: RuntimeEventKind;
  agent: string;
  summary: string;
  conversationId: string | null;
  data: Record<string, unknown>;
  createdAt: string;
  receivedAt: number;
}

export type RuntimeEventKind =
  | 'orchestration'
  | 'agent.started'
  | 'agent.completed'
  | 'agent.failed'
  | 'agent.delegated'
  | 'tool.requested'
  | 'tool.completed'
  | 'tool.failed'
  | 'approval.requested'
  | 'approval.approved'
  | 'approval.denied'
  | 'approval.expired'
  | 'provider.error'
  | 'turn.cancelled'
  | 'tool.timeout'
  | 'memory.read'
  | 'memory.write'
  | 'memory.search'
  | 'system.status'
  | 'error'
  | 'unknown';

/** Everything the Control Room renders. */
export interface JarvisRuntimeState {
  connection: ConnectionState;
  connectionDetail: string;
  lastConnectedAt: number | null;
  reconnectAttempts: number;
  snapshot: RuntimeStateSnapshot | null;
  activity: RuntimeActivity | null;
  telemetry: SystemTelemetry | null;
  agents: RuntimeAgentState[];
  approvals: ApprovalRequest[];
  providers: ProviderStatus[];
  recentEvents: RuntimeEvent[];
  /** Tool executions seen this session, newest first. */
  toolActivity: ToolActivityEntry[];
  memoryActivity: MemoryActivityEntry[];
  voice: VoiceState;
  /** Set when a command or approval call failed. Cleared on the next success. */
  lastCommandError: string | null;
  /** True while a command/brief is in flight. */
  commandInFlight: boolean;
  /** The turn currently running from this client, if any. Enables cancel. */
  activeTurnId: string | null;
  droppedEvents: number;
}

export interface ToolActivityEntry {
  callId: string;
  tool: string;
  agent: string;
  risk: RiskLevel | null;
  status: 'running' | 'completed' | 'failed' | 'awaiting_approval' | 'timeout' | 'cancelled';
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
  error: string | null;
}

export interface MemoryActivityEntry {
  id: string;
  kind: 'read' | 'write' | 'search';
  summary: string;
  agent: string;
  at: string;
}

/** The visual state the core sphere renders. Derived from runtime state only. */
export type CoreVisualState =
  | 'offline'
  | 'idle'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'approval_required'
  | 'error'
  | 'high_load';
