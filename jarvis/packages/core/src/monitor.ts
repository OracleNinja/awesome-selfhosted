/**
 * The runtime monitor — "what is JARVIS doing right now".
 *
 * Before this existed, the only record of activity was the event log: a list of
 * things that *had* happened. Anything wanting live state (which agents are
 * running, which tool calls are in flight, whether the system is idle or waiting
 * on a human) had to derive it by replaying events.
 *
 * That derivation belongs here, in the runtime, and not in a client. The Control
 * Room renders this state; it does not compute it. A second client — a CLI, a
 * phone app, a status LED — gets the same answer without reimplementing anything.
 *
 * The monitor is a pure event-bus subscriber. It never calls back into the
 * orchestrator, so it cannot affect execution.
 */
import type { EventBus, JarvisEvent, RiskLevel } from '@jarvis/shared';
import { now } from '@jarvis/shared';

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
  /** `provider` errors are a different problem from tool or agent errors. */
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
  /**
   * 0..1 activity intensity over the last few seconds, from the real event
   * rate. The Control Room's core visual uses this instead of a decorative
   * timer, so an idle system genuinely looks idle.
   */
  load: number;
}

const LOAD_WINDOW_MS = 10_000;
const LOAD_SATURATION = 12; // events within the window that count as "full load"

function emptyCounters(): RuntimeCounters {
  return {
    events: 0,
    userMessages: 0,
    modelResponses: 0,
    toolCalls: 0,
    toolFailures: 0,
    agentRuns: 0,
    approvalsRequested: 0,
    approvalsResolved: 0,
    memoryWrites: 0,
    memoryReads: 0,
    errors: 0,
  };
}

/** Classify an ERROR event so the UI can tell a provider outage from a tool bug. */
export function classifyError(event: JarvisEvent): RuntimeErrorInfo['kind'] {
  const kind = event.data.kind;
  if (
    kind === 'provider' ||
    kind === 'tool' ||
    kind === 'agent' ||
    kind === 'permission' ||
    kind === 'timeout'
  ) {
    return kind;
  }
  if (typeof event.data.reason === 'string' && /not configured|API key|base URL/i.test(event.data.reason)) {
    return 'provider';
  }
  if (event.data.tool) return 'tool';
  return 'unknown';
}

export class RuntimeMonitor {
  private phase: OrchestrationPhase = 'idle';
  private agents: ActiveAgentRun[] = [];
  private tools: ActiveToolExecution[] = [];
  private counters = emptyCounters();
  private lastError: RuntimeErrorInfo | null = null;
  private lastEventAt: string | null = null;
  private pendingApprovals = 0;
  private recentEventTimes: number[] = [];
  private unsubscribe: (() => void) | null = null;

  constructor(bus?: EventBus) {
    if (bus) this.attach(bus);
  }

  attach(bus: EventBus): void {
    this.unsubscribe?.();
    this.unsubscribe = bus.subscribe((event) => this.ingest(event));
  }

  detach(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
  }

  /** Fold one event into live state. Never throws — a monitor bug must not stop JARVIS. */
  ingest(event: JarvisEvent): void {
    try {
      this.counters.events += 1;
      this.lastEventAt = event.createdAt;
      this.recentEventTimes.push(Date.parse(event.createdAt) || Date.now());
      if (this.recentEventTimes.length > 200) this.recentEventTimes.shift();

      switch (event.type) {
        case 'USER_MESSAGE':
          this.counters.userMessages += 1;
          this.lastError = null;
          this.phase = 'thinking';
          break;

        case 'TOOL_REQUEST': {
          this.counters.toolCalls += 1;
          this.tools.push({
            callId: String(event.data.callId ?? event.id),
            tool: String(event.data.tool ?? 'unknown'),
            agent: event.agent,
            risk: (event.data.risk as RiskLevel) ?? 'READ',
            startedAt: event.createdAt,
            conversationId: event.conversationId,
          });
          this.phase = 'tool';
          break;
        }

        case 'TOOL_RESULT': {
          const callId = event.data.callId;
          const index = callId
            ? this.tools.findIndex((tool) => tool.callId === callId)
            : this.tools.findIndex((tool) => tool.tool === event.data.tool);
          if (index >= 0) this.tools.splice(index, 1);
          if (event.data.ok === false) this.counters.toolFailures += 1;
          this.settle();
          break;
        }

        case 'AGENT_DELEGATION':
          this.counters.agentRuns += 1;
          this.agents.push({
            agent: event.agent,
            summary: event.summary,
            startedAt: event.createdAt,
            conversationId: event.conversationId,
          });
          this.phase = 'delegating';
          break;

        case 'AGENT_RESULT': {
          const index = this.agents.findIndex((run) => run.agent === event.agent);
          if (index >= 0) this.agents.splice(index, 1);
          this.settle();
          break;
        }

        case 'APPROVAL_REQUEST':
          this.counters.approvalsRequested += 1;
          this.pendingApprovals += 1;
          this.phase = 'awaiting_approval';
          break;

        case 'APPROVAL_RESOLVED':
          this.counters.approvalsResolved += 1;
          this.pendingApprovals = Math.max(0, this.pendingApprovals - 1);
          this.settle();
          break;

        case 'MEMORY_WRITE':
          this.counters.memoryWrites += 1;
          break;

        case 'MEMORY_READ':
          this.counters.memoryReads += 1;
          break;

        case 'MODEL_RESPONSE':
          this.counters.modelResponses += 1;
          this.settle();
          break;

        case 'TURN_CANCELLED': {
          // A cancelled tool never sends a TOOL_RESULT; clear it here or it
          // stays "in flight" forever.
          this.clearCall(event);
          this.settle();
          break;
        }

        case 'ERROR':
          this.counters.errors += 1;
          // Same for a timed-out call: the boundary aborted it, so no result
          // event is coming.
          if (event.data.callId) {
            this.clearCall(event);
            if (event.data.outcome === 'timeout') this.counters.toolFailures += 1;
          }
          this.lastError = {
            summary: event.summary,
            agent: event.agent,
            kind: classifyError(event),
            at: event.createdAt,
          };
          this.phase = 'error';
          break;

        default:
          break;
      }
    } catch {
      /* observation must never break execution */
    }
  }

  /** Drop an in-flight tool entry that will never receive a result. */
  private clearCall(event: JarvisEvent): void {
    const callId = event.data.callId;
    const index = callId
      ? this.tools.findIndex((tool) => tool.callId === callId)
      : this.tools.findIndex((tool) => tool.tool === event.data.tool);
    if (index >= 0) this.tools.splice(index, 1);
  }

  /** Return to the least-busy phase consistent with what is still outstanding. */
  private settle(): void {
    if (this.pendingApprovals > 0) this.phase = 'awaiting_approval';
    else if (this.tools.length > 0) this.phase = 'tool';
    else if (this.agents.length > 0) this.phase = 'delegating';
    else this.phase = 'idle';
  }

  /**
   * Correct the pending-approval count from the authoritative store.
   *
   * The monitor's own count is derived from events and would drift across a
   * restart or an expiry sweep; the database is the truth.
   */
  syncPendingApprovals(count: number): void {
    this.pendingApprovals = Math.max(0, count);
    if (this.phase === 'idle' && count > 0) this.phase = 'awaiting_approval';
    if (this.phase === 'awaiting_approval' && count === 0) this.settle();
  }

  private computeLoad(reference = Date.now()): number {
    const cutoff = reference - LOAD_WINDOW_MS;
    const recent = this.recentEventTimes.filter((time) => time >= cutoff).length;
    return Math.min(1, recent / LOAD_SATURATION);
  }

  snapshot(reference = Date.now()): RuntimeActivity {
    return {
      phase: this.phase,
      busy: this.phase !== 'idle' && this.phase !== 'error',
      activeAgents: [...this.agents],
      activeTools: [...this.tools],
      lastError: this.lastError,
      counters: { ...this.counters },
      lastEventAt: this.lastEventAt,
      load: this.computeLoad(reference),
    };
  }

  /** Used when a conversation ends abnormally, and by tests. */
  reset(): void {
    this.phase = 'idle';
    this.agents = [];
    this.tools = [];
    this.lastError = null;
    this.pendingApprovals = 0;
    this.recentEventTimes = [];
    this.lastEventAt = now();
  }
}
