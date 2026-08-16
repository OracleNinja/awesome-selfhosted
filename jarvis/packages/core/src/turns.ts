/**
 * Turn control: the cancellation boundary for a unit of user work.
 *
 * Before this existed, `AbortSignal` was threaded through most of the runtime
 * but nothing ever created a controller, so a turn could be plumbed for
 * cancellation and still be impossible to stop. A turn now owns exactly one
 * controller; every model call and every tool call belonging to that turn
 * receives its signal, and cancelling the turn aborts the whole tree.
 *
 * The controller never leaves this module. Callers get a `TurnHandle`, which
 * exposes the read-only signal and an idempotent `end()` — so no caller can
 * abort another turn's work by holding its controller.
 */
import { DEFAULT_TOOL_TIMEOUT_MS, id, now } from '@jarvis/shared';

/**
 * The smallest thing that must travel with a unit of work.
 *
 * Deliberately two fields. `turnId` answers "what does this belong to";
 * `signal` answers "should it still be running". Anything else about a turn is
 * already reachable from the store by its id, and putting it here would make
 * this a context bag rather than a control boundary.
 */
export interface TurnContext {
  readonly turnId: string;
  readonly signal: AbortSignal;
  /**
   * The turn that caused this one.
   *
   * Set when a turn was started by a decision on another turn's approval. The
   * approved action is genuinely its own turn — a human started it, and it has
   * its own cancellation boundary — so `turnId` keeps its meaning and the
   * causal link is recorded separately.
   */
  readonly parentTurnId: string | null;
}

/** Why a turn stopped. Cancellation is never collapsed into a generic error. */
export type TurnOutcome = 'completed' | 'cancelled' | 'failed';

/** Why an abort fired. Recorded once, first writer wins, so races are deterministic. */
export type AbortCause = 'turn_cancelled' | 'tool_timeout';

export type CancelResult = 'cancelled' | 'already_cancelled' | 'already_finished' | 'not_found';

export class TurnCancelledError extends Error {
  readonly code = 'turn_cancelled';
  readonly turnId: string;
  constructor(turnId: string, reason?: string) {
    super(reason ? `turn ${turnId} was cancelled: ${reason}` : `turn ${turnId} was cancelled`);
    this.name = 'TurnCancelledError';
    this.turnId = turnId;
  }
}

export class ToolTimeoutError extends Error {
  readonly code = 'tool_timeout';
  readonly tool: string;
  readonly timeoutMs: number;
  constructor(tool: string, timeoutMs: number) {
    super(`tool "${tool}" exceeded its ${timeoutMs}ms timeout and was aborted`);
    this.name = 'ToolTimeoutError';
    this.tool = tool;
    this.timeoutMs = timeoutMs;
  }
}

export interface TurnInfo {
  turnId: string;
  parentTurnId: string | null;
  userId: string;
  conversationId: string | null;
  startedAt: string;
  cancelled: boolean;
  cancelReason: string | null;
}

/**
 * A live turn. The only handle callers ever hold.
 *
 * `end()` is idempotent and safe to call after cancellation — the common path
 * is a `finally` block that does not know how the turn finished.
 */
export class TurnHandle {
  readonly turnId: string;
  readonly parentTurnId: string | null;
  readonly userId: string;
  readonly conversationId: string | null;
  readonly startedAt: string;
  readonly #controller: AbortController;
  private readonly onEnd: (turnId: string) => void;
  private cancelReason: string | null = null;
  private cancelled = false;
  private finished = false;

  constructor(options: {
    turnId: string;
    userId: string;
    conversationId: string | null;
    parentTurnId?: string | null;
    onEnd: (turnId: string) => void;
  }) {
    this.turnId = options.turnId;
    this.parentTurnId = options.parentTurnId ?? null;
    this.userId = options.userId;
    this.conversationId = options.conversationId;
    this.startedAt = now();
    this.#controller = new AbortController();
    this.onEnd = options.onEnd;
  }

  /**
   * Read-only. The controller is a genuine private field, not a TypeScript
   * `private` — so no caller, typed or not, can reach in and abort another
   * turn's work.
   */
  get signal(): AbortSignal {
    return this.#controller.signal;
  }

  get context(): TurnContext {
    return { turnId: this.turnId, signal: this.#controller.signal, parentTurnId: this.parentTurnId };
  }

  isCancelled(): boolean {
    return this.cancelled;
  }

  isFinished(): boolean {
    return this.finished;
  }

  get reason(): string | null {
    return this.cancelReason;
  }

  /** Internal — only the registry calls this. Idempotent. */
  cancel(reason?: string): boolean {
    if (this.cancelled || this.finished) return false;
    this.cancelled = true;
    this.cancelReason = reason ?? 'cancelled by user';
    this.#controller.abort(new TurnCancelledError(this.turnId, this.cancelReason));
    return true;
  }

  /**
   * Release the turn. Idempotent, and safe on every path — completion, error
   * and cancellation all end here, which is what keeps controllers from leaking.
   */
  end(): void {
    if (this.finished) return;
    this.finished = true;
    this.onEnd(this.turnId);
  }

  info(): TurnInfo {
    return {
      turnId: this.turnId,
      parentTurnId: this.parentTurnId,
      userId: this.userId,
      conversationId: this.conversationId,
      startedAt: this.startedAt,
      cancelled: this.cancelled,
      cancelReason: this.cancelReason,
    };
  }
}

/**
 * The registry of in-flight turns.
 *
 * Cancellation is by id, so a caller that never saw the handle (an HTTP route,
 * the Control Room) can still stop work. Entries are removed on every exit
 * path, so the map size is a live count of running turns, not a leak.
 */
export class TurnRegistry {
  private turns = new Map<string, TurnHandle>();

  begin(options: {
    userId: string;
    conversationId?: string | null;
    turnId?: string;
    /** Set when this turn was started by a decision on another turn's approval. */
    parentTurnId?: string | null;
  }): TurnHandle {
    const turnId = options.turnId ?? id('turn');
    const handle = new TurnHandle({
      turnId,
      userId: options.userId,
      conversationId: options.conversationId ?? null,
      parentTurnId: options.parentTurnId ?? null,
      onEnd: (finished) => {
        this.turns.delete(finished);
      },
    });
    this.turns.set(turnId, handle);
    return handle;
  }

  /**
   * Cancel a turn by id.
   *
   * Every outcome is a value, never a throw: cancelling an unknown or already
   * finished turn is a normal thing for a UI to do (the turn may have completed
   * between render and click) and must not surface as an internal error.
   */
  cancel(turnId: string, reason?: string): CancelResult {
    const handle = this.turns.get(turnId);
    if (!handle) return 'not_found';
    if (handle.isFinished()) return 'already_finished';
    if (handle.isCancelled()) return 'already_cancelled';
    handle.cancel(reason);
    return 'cancelled';
  }

  get(turnId: string): TurnHandle | undefined {
    return this.turns.get(turnId);
  }

  has(turnId: string): boolean {
    return this.turns.has(turnId);
  }

  /** Live count of in-flight turns. A leak would show up here. */
  get size(): number {
    return this.turns.size;
  }

  active(userId?: string): TurnInfo[] {
    return [...this.turns.values()]
      .filter((handle) => !userId || handle.userId === userId)
      .map((handle) => handle.info());
  }

  /** Cancel everything — used on shutdown. */
  cancelAll(reason = 'runtime shutting down'): number {
    let count = 0;
    for (const handle of [...this.turns.values()]) {
      if (handle.cancel(reason)) count += 1;
    }
    return count;
  }
}

// ---------------------------------------------------------------------------
// Tool timeout
// ---------------------------------------------------------------------------

// Declared in @jarvis/shared so both core and tools reach it without a cycle.
export { DEFAULT_TOOL_TIMEOUT_MS } from '@jarvis/shared';

export interface LinkedTimeout {
  /** Signal handed to the tool. Aborts on turn cancellation or on timeout. */
  readonly signal: AbortSignal;
  /**
   * Which side aborted, recorded once at the moment it happened.
   *
   * This is what makes the timeout/cancellation race deterministic: if the user
   * cancels while a tool is running, the cause stays `turn_cancelled` even
   * though the timer fires afterwards, and vice versa.
   */
  cause(): AbortCause | null;
  /** Clears the timer and detaches the listener. Must be called in a finally. */
  dispose(): void;
}

/**
 * Give a tool call its own abort boundary, linked to the turn.
 *
 * Aborts when either the turn is cancelled or the timeout elapses, whichever
 * happens first — and remembers which, so the outcome can be classified
 * correctly rather than guessed at afterwards.
 */
export function linkTimeout(
  turnSignal: AbortSignal | undefined,
  timeoutMs: number,
  tool: string,
): LinkedTimeout {
  const controller = new AbortController();
  let cause: AbortCause | null = null;

  const abortWith = (nextCause: AbortCause, error: Error) => {
    // First writer wins. A second abort cannot reclassify the first.
    if (cause !== null || controller.signal.aborted) return;
    cause = nextCause;
    controller.abort(error);
  };

  const onTurnAbort = () => {
    const reason = turnSignal?.reason;
    abortWith(
      'turn_cancelled',
      reason instanceof Error ? reason : new TurnCancelledError('unknown', 'turn cancelled'),
    );
  };

  // Cancel-before-start: if the turn is already aborted, the tool signal starts
  // aborted too and the executor refuses to run it.
  if (turnSignal?.aborted) {
    onTurnAbort();
  } else if (turnSignal) {
    turnSignal.addEventListener('abort', onTurnAbort, { once: true });
  }

  const timer =
    timeoutMs > 0 && !controller.signal.aborted
      ? setTimeout(() => abortWith('tool_timeout', new ToolTimeoutError(tool, timeoutMs)), timeoutMs)
      : null;
  // Housekeeping must never hold the process open.
  timer?.unref?.();

  return {
    signal: controller.signal,
    cause: () => cause,
    dispose: () => {
      if (timer) clearTimeout(timer);
      turnSignal?.removeEventListener('abort', onTurnAbort);
    },
  };
}

/** True when an error came from an abort rather than from the operation itself. */
export function isAbortError(error: unknown): boolean {
  if (error instanceof TurnCancelledError || error instanceof ToolTimeoutError) return true;
  const name = (error as Error | undefined)?.name;
  return name === 'AbortError' || name === 'TimeoutError';
}
