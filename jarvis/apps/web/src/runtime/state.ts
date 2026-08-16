/**
 * The runtime store.
 *
 * A plain external store with `subscribe`/`getSnapshot`, consumed through
 * `useSyncExternalStore`. React holds no runtime state of its own: this is a
 * projection of what the runtime reported, and every transition is a pure
 * function of (previous projection, runtime message).
 *
 * Two properties this buys us, both required by the brief:
 *
 *  - Components subscribe to slices, so a burst of events does not rerender the
 *    whole application (Phase 19).
 *  - The canvas reads `getState()` inside its own animation frame without
 *    causing a React render at all.
 */
import { normalizeEvent } from './events';
import type {
  ApprovalRequest,
  ConnectionState,
  CoreVisualState,
  JarvisRuntimeState,
  MemoryActivityEntry,
  RuntimeEvent,
  RuntimeStateSnapshot,
  ToolActivityEntry,
  VoicePhase,
  VoiceState,
} from './types';

const MAX_EVENTS = 300;
const MAX_TOOL_ACTIVITY = 80;
const MAX_MEMORY_ACTIVITY = 40;

const initialVoice: VoiceState = {
  phase: 'idle',
  source: 'browser',
  sttMode: 'browser',
  ttsMode: 'browser',
  sttAvailable: false,
  ttsAvailable: false,
  supported: false,
  transcript: '',
  error: null,
};

export function initialState(): JarvisRuntimeState {
  return {
    connection: 'connecting',
    connectionDetail: 'Connecting to the JARVIS runtime…',
    lastConnectedAt: null,
    reconnectAttempts: 0,
    snapshot: null,
    activity: null,
    telemetry: null,
    agents: [],
    approvals: [],
    providers: [],
    recentEvents: [],
    toolActivity: [],
    memoryActivity: [],
    voice: initialVoice,
    lastCommandError: null,
    commandInFlight: false,
    activeTurnId: null,
    droppedEvents: 0,
  };
}

export type RuntimeStoreListener = () => void;

export class RuntimeStore {
  private state: JarvisRuntimeState = initialState();
  private listeners = new Set<RuntimeStoreListener>();

  getState(): JarvisRuntimeState {
    return this.state;
  }

  subscribe = (listener: RuntimeStoreListener): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private set(patch: Partial<JarvisRuntimeState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) {
      try {
        listener();
      } catch {
        /* a broken subscriber must not stop the stream */
      }
    }
  }

  // ------------------------------------------------------------- connection

  setConnection(connection: ConnectionState, detail: string): void {
    const patch: Partial<JarvisRuntimeState> = { connection, connectionDetail: detail };
    if (connection === 'online') {
      patch.lastConnectedAt = Date.now();
      patch.reconnectAttempts = 0;
    }
    this.set(patch);
  }

  noteReconnectAttempt(): number {
    const attempts = this.state.reconnectAttempts + 1;
    this.set({ reconnectAttempts: attempts });
    return attempts;
  }

  // ------------------------------------------------------------- snapshots

  /**
   * Apply a full runtime snapshot.
   *
   * Sent on connect and on every reconnect, which is what makes reconnection
   * correct: the client never has to infer what it missed while disconnected,
   * it is simply told the current truth.
   */
  applySnapshot(snapshot: RuntimeStateSnapshot): void {
    const historical = [...snapshot.recentEvents]
      .map((event) => normalizeEvent(event))
      .filter((event): event is RuntimeEvent => event !== null);

    // Snapshot events are newest-first from the store; keep that ordering and
    // drop any this session has already seen.
    const seen = new Set(this.state.recentEvents.map((event) => event.id));
    const merged = [...this.state.recentEvents, ...historical.filter((e) => !seen.has(e.id))].sort(
      (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt),
    );

    this.set({
      snapshot,
      activity: snapshot.activity,
      telemetry: snapshot.telemetry,
      agents: snapshot.agents,
      approvals: snapshot.approvals,
      providers: snapshot.providers,
      recentEvents: merged.slice(0, MAX_EVENTS),
      voice: {
        ...this.state.voice,
        source: snapshot.voice.stt.mode === 'server' ? 'server' : 'browser',
        sttMode: snapshot.voice.stt.mode,
        ttsMode: snapshot.voice.tts.mode,
        sttAvailable: snapshot.voice.stt.available,
        ttsAvailable: snapshot.voice.tts.available,
      },
    });
  }

  applyTelemetry(payload: {
    telemetry: JarvisRuntimeState['telemetry'];
    activity: JarvisRuntimeState['activity'];
  }): void {
    const patch: Partial<JarvisRuntimeState> = {};
    if (payload.telemetry) patch.telemetry = payload.telemetry;
    // The activity sample keeps phase/load fresh between events, so an idle
    // system's load decays visibly instead of freezing at its last value.
    if (payload.activity) patch.activity = payload.activity;
    this.set(patch);
  }

  // ----------------------------------------------------------------- events

  /** Ingest one raw frame. Returns false if it was malformed and dropped. */
  ingestRaw(raw: unknown): boolean {
    const event = normalizeEvent(raw);
    if (!event) {
      this.set({ droppedEvents: this.state.droppedEvents + 1 });
      return false;
    }
    this.ingest(event);
    return true;
  }

  ingest(event: RuntimeEvent): void {
    if (this.state.recentEvents.some((existing) => existing.id === event.id)) return;

    const patch: Partial<JarvisRuntimeState> = {
      recentEvents: [event, ...this.state.recentEvents].slice(0, MAX_EVENTS),
    };

    const toolActivity = this.foldToolActivity(event);
    if (toolActivity) patch.toolActivity = toolActivity;

    const memoryActivity = this.foldMemoryActivity(event);
    if (memoryActivity) patch.memoryActivity = memoryActivity;

    // Approvals are authoritative from the runtime; an event only tells us the
    // list is stale. The client re-reads rather than editing a local copy.
    this.set(patch);
  }

  private foldToolActivity(event: RuntimeEvent): ToolActivityEntry[] | null {
    const callId = typeof event.data.callId === 'string' ? event.data.callId : null;
    const tool = typeof event.data.tool === 'string' ? event.data.tool : null;

    if (event.kind === 'tool.requested' && tool) {
      const entry: ToolActivityEntry = {
        callId: callId ?? event.id,
        tool,
        agent: event.agent,
        risk: (event.data.risk as ToolActivityEntry['risk']) ?? null,
        status: 'running',
        startedAt: event.createdAt,
        finishedAt: null,
        durationMs: null,
        error: null,
      };
      return [entry, ...this.state.toolActivity].slice(0, MAX_TOOL_ACTIVITY);
    }

    if ((event.kind === 'tool.completed' || event.kind === 'tool.failed') && tool) {
      const index = this.state.toolActivity.findIndex((entry) =>
        callId ? entry.callId === callId : entry.tool === tool && entry.status === 'running',
      );
      if (index < 0) return null;
      const next = [...this.state.toolActivity];
      const existing = next[index]!;
      next[index] = {
        ...existing,
        status: event.kind === 'tool.failed' ? 'failed' : 'completed',
        finishedAt: event.createdAt,
        durationMs: typeof event.data.durationMs === 'number' ? event.data.durationMs : null,
        error: typeof event.data.error === 'string' ? event.data.error : null,
      };
      return next;
    }

    if (event.kind === 'approval.requested' && tool) {
      const index = this.state.toolActivity.findIndex((entry) =>
        callId ? entry.callId === callId : entry.tool === tool && entry.status === 'running',
      );
      if (index < 0) return null;
      const next = [...this.state.toolActivity];
      next[index] = { ...next[index]!, status: 'awaiting_approval' };
      return next;
    }

    return null;
  }

  private foldMemoryActivity(event: RuntimeEvent): MemoryActivityEntry[] | null {
    const kind =
      event.kind === 'memory.write'
        ? 'write'
        : event.kind === 'memory.search'
          ? 'search'
          : event.kind === 'memory.read'
            ? 'read'
            : null;
    if (!kind) return null;

    const entry: MemoryActivityEntry = {
      id: event.id,
      kind,
      // Summaries come from the runtime and are already redacted. The panel
      // shows activity, not memory contents — see the Memory view for those.
      summary: event.summary,
      agent: event.agent,
      at: event.createdAt,
    };
    return [entry, ...this.state.memoryActivity].slice(0, MAX_MEMORY_ACTIVITY);
  }

  // -------------------------------------------------------------- approvals

  setApprovals(approvals: ApprovalRequest[]): void {
    this.set({ approvals });
  }

  // ------------------------------------------------------------------ voice

  setVoice(patch: Partial<VoiceState>): void {
    this.set({ voice: { ...this.state.voice, ...patch } });
  }

  setVoicePhase(phase: VoicePhase, error: string | null = null): void {
    this.set({ voice: { ...this.state.voice, phase, error } });
  }

  // ---------------------------------------------------------------- commands

  setCommandInFlight(inFlight: boolean, turnId: string | null = null): void {
    this.set({ commandInFlight: inFlight, activeTurnId: inFlight ? turnId : null });
  }

  setCommandError(message: string | null): void {
    this.set({ lastCommandError: message });
  }

  reset(): void {
    this.state = initialState();
    for (const listener of this.listeners) listener();
  }
}

// ---------------------------------------------------------------------------
// Derived selectors
// ---------------------------------------------------------------------------

/**
 * Runtime state → core visual state.
 *
 * The single place the sphere's appearance is decided. Every input is real
 * runtime state; there is no timer and no "look busy" fallback, so an idle
 * system looks idle.
 */
export function coreVisualState(state: JarvisRuntimeState): CoreVisualState {
  if (state.connection === 'offline' || state.connection === 'unauthorized') return 'offline';

  // Voice takes visual priority: the user is mid-interaction and needs feedback
  // about the microphone more than about background load.
  if (state.voice.phase === 'listening') return 'listening';
  if (state.voice.phase === 'speaking') return 'speaking';

  if (state.approvals.length > 0) return 'approval_required';

  const activity = state.activity;
  if (!activity) return state.connection === 'connecting' ? 'idle' : 'idle';

  if (activity.phase === 'error') return 'error';
  if (activity.phase === 'awaiting_approval') return 'approval_required';

  if (activity.busy) return activity.load >= 0.75 ? 'high_load' : 'processing';
  if (state.voice.phase === 'processing') return 'processing';

  return activity.load >= 0.75 ? 'high_load' : 'idle';
}

/** Numeric intensity for the animation, 0..1. Real load, not decoration. */
export function coreIntensity(state: JarvisRuntimeState): number {
  const activity = state.activity;
  const base = activity ? activity.load : 0;
  const visual = coreVisualState(state);
  const floor =
    visual === 'offline'
      ? 0
      : visual === 'listening' || visual === 'speaking'
        ? 0.55
        : visual === 'processing'
          ? 0.5
          : visual === 'high_load'
            ? 0.85
            : visual === 'approval_required'
              ? 0.35
              : 0.08;
  return Math.min(1, Math.max(floor, base));
}

export function connectionLabel(state: JarvisRuntimeState): {
  label: 'ONLINE' | 'OFFLINE' | 'DEGRADED' | 'CONNECTING' | 'UNAUTHORIZED';
  tone: 'ok' | 'warn' | 'bad';
} {
  switch (state.connection) {
    case 'online': {
      const providerDown = state.snapshot?.activeModel.available === false;
      const errored = state.activity?.lastError?.kind === 'provider';
      return providerDown || errored
        ? { label: 'DEGRADED', tone: 'warn' }
        : { label: 'ONLINE', tone: 'ok' };
    }
    case 'degraded':
      return { label: 'DEGRADED', tone: 'warn' };
    case 'connecting':
      return { label: 'CONNECTING', tone: 'warn' };
    case 'unauthorized':
      return { label: 'UNAUTHORIZED', tone: 'bad' };
    case 'offline':
    default:
      return { label: 'OFFLINE', tone: 'bad' };
  }
}
