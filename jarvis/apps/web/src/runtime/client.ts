/**
 * The JARVIS runtime client.
 *
 * Everything the Control Room knows about the runtime arrives through this
 * object: one managed SSE connection in, HTTP commands out. Components never
 * touch `fetch` or `EventSource` — which is what stops connection handling from
 * being reinvented per panel, and keeps the number of live streams at one no
 * matter how many components are mounted.
 *
 * `fetch` and `EventSource` are injectable so the reconnect, disconnect and
 * malformed-frame paths can be tested in node without a browser.
 */
import { api, ApiError, request } from './api';
import { RuntimeStore } from './state';
import type { ApprovalRequest, RuntimeStateSnapshot } from './types';

export interface EventSourceLike {
  addEventListener(type: string, listener: (event: { data: string }) => void): void;
  close(): void;
  onerror: ((event: unknown) => void) | null;
  onopen: ((event: unknown) => void) | null;
}

export interface RuntimeClientOptions {
  store?: RuntimeStore;
  /** Injected for tests; defaults to the browser's EventSource. */
  eventSourceFactory?: (url: string) => EventSourceLike;
  /** Backoff schedule in ms. The last value repeats. */
  reconnectDelays?: number[];
  /** Injected for tests so reconnects do not depend on wall-clock waits. */
  setTimeoutImpl?: (fn: () => void, ms: number) => unknown;
  clearTimeoutImpl?: (handle: unknown) => void;
  tokenProvider?: () => string;
}

const DEFAULT_BACKOFF = [1000, 2000, 5000, 10_000, 30_000];

/** The prompt BRIEF ME sends. A normal user turn — no separate briefing engine. */
export const BRIEFING_PROMPT =
  'Give me a status brief. Check the current time, review my open tasks and anything ' +
  'in long-term memory that is still relevant, and tell me what needs my attention now. ' +
  'If a capability you would need is not configured, say so rather than guessing.';

export class JarvisRuntimeClient {
  readonly store: RuntimeStore;
  private source: EventSourceLike | null = null;
  private reconnectHandle: unknown = null;
  private closed = false;
  private readonly backoff: number[];
  private readonly makeEventSource: (url: string) => EventSourceLike;
  private readonly setTimeoutImpl: (fn: () => void, ms: number) => unknown;
  private readonly clearTimeoutImpl: (handle: unknown) => void;
  private readonly tokenProvider: () => string;

  constructor(options: RuntimeClientOptions = {}) {
    this.store = options.store ?? new RuntimeStore();
    this.backoff = options.reconnectDelays ?? DEFAULT_BACKOFF;
    this.makeEventSource =
      options.eventSourceFactory ??
      ((url: string) => new EventSource(url) as unknown as EventSourceLike);
    this.setTimeoutImpl =
      options.setTimeoutImpl ?? ((fn, ms) => setTimeout(fn, ms) as unknown);
    this.clearTimeoutImpl =
      options.clearTimeoutImpl ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
    this.tokenProvider = options.tokenProvider ?? (() => {
      try {
        return localStorage.getItem('jarvis.apiToken') ?? '';
      } catch {
        return '';
      }
    });
  }

  // ------------------------------------------------------------- lifecycle

  /** Fetch an initial snapshot, then open the stream. Safe to call once. */
  async connect(): Promise<void> {
    this.closed = false;
    this.store.setConnection('connecting', 'Connecting to the JARVIS runtime…');
    await this.refreshState();
    this.openStream();
  }

  disconnect(): void {
    this.closed = true;
    if (this.reconnectHandle) {
      this.clearTimeoutImpl(this.reconnectHandle);
      this.reconnectHandle = null;
    }
    this.source?.close();
    this.source = null;
    this.store.setConnection('offline', 'Disconnected.');
  }

  private streamUrl(): string {
    const token = this.tokenProvider();
    return token
      ? `/api/events/stream?token=${encodeURIComponent(token)}`
      : '/api/events/stream';
  }

  private openStream(): void {
    if (this.closed) return;
    // A rejected token is a terminal answer, not a transport failure. The stream
    // would fail the same way, and its generic error handler would replace an
    // actionable diagnosis ("enter your token in Settings") with a reconnect
    // countdown that can never succeed on its own. Both connect() and
    // reconnect() come through here, so one guard covers both.
    if (this.store.getState().connection === 'unauthorized') return;
    this.source?.close();

    const source = this.makeEventSource(this.streamUrl());
    this.source = source;

    source.onopen = () => {
      this.store.setConnection('online', 'Connected to the JARVIS runtime.');
    };

    // A full snapshot on connect (and on every reconnect) is what makes
    // reconnection correct: the client is told the current truth rather than
    // guessing at what happened while it was away.
    source.addEventListener('state', (message) => {
      const snapshot = this.parse<RuntimeStateSnapshot>(message.data);
      if (!snapshot) {
        this.store.ingestRaw(null); // record the dropped frame
        return;
      }
      this.store.applySnapshot(snapshot);
      this.store.setConnection('online', 'Connected to the JARVIS runtime.');
    });

    source.addEventListener('jarvis', (message) => {
      const parsed = this.parse<unknown>(message.data);
      // ingestRaw validates; a malformed frame is counted, not thrown on.
      // An exception here would kill the subscription silently.
      this.store.ingestRaw(parsed);
      if (this.needsApprovalRefresh(parsed)) void this.refreshApprovals();
    });

    source.addEventListener('telemetry', (message) => {
      const payload = this.parse<{
        telemetry: RuntimeStateSnapshot['telemetry'];
        activity: RuntimeStateSnapshot['activity'];
      }>(message.data);
      if (payload) this.store.applyTelemetry(payload);
      else this.store.ingestRaw(null);
    });

    source.onerror = () => {
      if (this.closed) return;
      this.source?.close();
      this.source = null;
      const attempt = this.store.noteReconnectAttempt();
      const delay = this.backoff[Math.min(attempt - 1, this.backoff.length - 1)] ?? 30_000;
      this.store.setConnection(
        'offline',
        `Lost the runtime connection. Reconnecting in ${Math.round(delay / 1000)}s (attempt ${attempt}).`,
      );
      this.reconnectHandle = this.setTimeoutImpl(() => {
        this.reconnectHandle = null;
        void this.reconnect();
      }, delay);
    };
  }

  /** Re-fetch state first so a reconnect never renders stale data as live. */
  private async reconnect(): Promise<void> {
    if (this.closed) return;
    this.store.setConnection('connecting', 'Reconnecting…');
    await this.refreshState();
    this.openStream();
  }

  /** Parse a frame body. Returns null on malformed JSON; the caller counts it. */
  private parse<T>(data: string): T | null {
    try {
      return JSON.parse(data) as T;
    } catch {
      return null;
    }
  }

  private needsApprovalRefresh(raw: unknown): boolean {
    if (!raw || typeof raw !== 'object') return false;
    const type = (raw as { type?: string }).type;
    return type === 'APPROVAL_REQUEST' || type === 'APPROVAL_RESOLVED';
  }

  // ---------------------------------------------------------------- reads

  async refreshState(): Promise<RuntimeStateSnapshot | null> {
    try {
      const snapshot = await api.runtimeState();
      this.store.applySnapshot(snapshot);
      this.store.setConnection('online', 'Connected to the JARVIS runtime.');
      return snapshot;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        // "No token" and "wrong token" need different instructions, and the
        // first is what every operator hits the moment they set
        // JARVIS_API_TOKEN — which PRODUCTION.md requires for any non-loopback
        // deployment.
        this.store.setConnection(
          'unauthorized',
          this.tokenProvider()
            ? 'The runtime rejected this token. Enter a valid JARVIS_API_TOKEN in Settings.'
            : 'This runtime requires an API token. Open Settings and enter the JARVIS_API_TOKEN the server was started with.',
        );
      } else {
        this.store.setConnection(
          'offline',
          `Cannot reach the JARVIS runtime: ${(error as Error).message}`,
        );
      }
      return null;
    }
  }

  async refreshApprovals(): Promise<ApprovalRequest[]> {
    try {
      const result = await api.approvals();
      this.store.setApprovals(result.approvals);
      return result.approvals;
    } catch {
      return this.store.getState().approvals;
    }
  }

  // -------------------------------------------------------------- commands

  /**
   * Send a user message through the orchestrator.
   *
   * The reply is not interpreted here — the runtime decides whether to answer,
   * call a tool, delegate or stop for approval, and the Control Room watches
   * that happen on the event stream.
   */
  async sendCommand(
    text: string,
    conversationId?: string | null,
  ): Promise<{ ok: boolean; conversationId?: string; reply?: string; error?: string; turnId?: string }> {
    // The turn id is minted here and sent with the request, so the user can
    // cancel before the response returns — which is the only moment cancelling
    // is actually useful.
    const turnId = `turn_${crypto.randomUUID().replace(/-/g, '').slice(0, 20)}`;
    this.store.setCommandInFlight(true, turnId);
    this.store.setCommandError(null);
    try {
      const turn = await api.chat(text, conversationId ?? null, turnId);
      // The turn carries the authoritative pending-approval list for it.
      this.store.setApprovals(turn.pendingApprovals);
      if (turn.error) this.store.setCommandError(turn.error);
      const result: {
        ok: boolean;
        conversationId?: string;
        reply?: string;
        error?: string;
        turnId?: string;
      } = {
        // A cancelled turn is not a failure — the user asked for it.
        ok: !turn.error && turn.outcome !== 'failed',
        conversationId: turn.conversationId,
        reply: turn.reply,
        turnId: turn.turnId,
      };
      if (turn.error) result.error = turn.error;
      return result;
    } catch (error) {
      const message = (error as Error).message;
      this.store.setCommandError(message);
      return { ok: false, error: message };
    } finally {
      this.store.setCommandInFlight(false);
      void this.refreshState();
    }
  }

  /**
   * Cancel the turn this client is waiting on.
   *
   * The runtime decides the outcome; a turn that finished between render and
   * click reports `already_finished`, which is information, not an error.
   */
  async cancelTurn(turnId?: string): Promise<{ status: string } | null> {
    const target = turnId ?? this.store.getState().activeTurnId;
    if (!target) return null;
    try {
      return await api.cancelTurn(target, 'cancelled from the Control Room');
    } catch (error) {
      this.store.setCommandError(`Cancel failed: ${(error as Error).message}`);
      return null;
    }
  }

  /** BRIEF ME — an ordinary runtime turn, not a separate briefing path. */
  async brief(conversationId?: string | null) {
    return this.sendCommand(BRIEFING_PROMPT, conversationId ?? null);
  }

  /**
   * Approve a pending request.
   *
   * The UI state is not updated optimistically: the runtime executes the
   * action, and the resulting state is read back. A click is a request, not an
   * outcome.
   */
  async approve(approvalId: string, note?: string): Promise<{ ok: boolean; message: string }> {
    return this.decide('approve', approvalId, note);
  }

  async deny(approvalId: string, note?: string): Promise<{ ok: boolean; message: string }> {
    return this.decide('deny', approvalId, note);
  }

  private async decide(
    decision: 'approve' | 'deny',
    approvalId: string,
    note?: string,
  ): Promise<{ ok: boolean; message: string }> {
    try {
      const outcome =
        decision === 'approve' ? await api.approve(approvalId, note) : await api.deny(approvalId, note);
      if (!outcome.ok && outcome.state === 'unavailable') {
        this.store.setCommandError(outcome.message);
      }
      return { ok: outcome.ok, message: outcome.message };
    } catch (error) {
      // Includes the runtime's 409 ("already approved", "expired"), which is a
      // legitimate answer rather than a transport failure.
      const message = (error as Error).message;
      this.store.setCommandError(`Approval failed: ${message}`);
      return { ok: false, message };
    } finally {
      // Read the truth back from the runtime whichever way it went.
      await this.refreshApprovals();
      void this.refreshState();
    }
  }

  async runAgent(name: string, task: string, conversationId?: string | null) {
    this.store.setCommandInFlight(true);
    try {
      return await api.runAgent(name, task, conversationId ?? null);
    } finally {
      this.store.setCommandInFlight(false);
      void this.refreshState();
    }
  }

  /** Raw escape hatch for views that need an endpoint not wrapped above. */
  request = request;
}
