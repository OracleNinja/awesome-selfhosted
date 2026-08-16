/**
 * The internal event stream.
 *
 * Every meaningful thing the orchestrator does emits a structured event.
 * The activity feed, the audit log and the SSE stream all read from here, so
 * there is exactly one description of "what JARVIS did".
 */
import type { EventType, JarvisEvent } from './types.ts';
import { id, now } from './util.ts';

export type EventListener = (event: JarvisEvent) => void;

export interface EmitOptions {
  type: EventType;
  userId: string;
  /** Correlates this event with the user turn that caused it. */
  turnId?: string | null;
  /** Set when this turn was started by a decision on another turn's approval. */
  parentTurnId?: string | null;
  conversationId?: string | null;
  agent?: string;
  summary: string;
  data?: Record<string, unknown>;
}

export class EventBus {
  private listeners = new Set<EventListener>();
  private ring: JarvisEvent[] = [];
  private readonly ringSize: number;

  constructor(ringSize = 500) {
    this.ringSize = ringSize;
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  emit(options: EmitOptions): JarvisEvent {
    const event: JarvisEvent = {
      id: id('evt'),
      type: options.type,
      turnId: options.turnId ?? null,
      parentTurnId: options.parentTurnId ?? null,
      conversationId: options.conversationId ?? null,
      userId: options.userId,
      agent: options.agent ?? 'jarvis',
      summary: options.summary,
      data: options.data ?? {},
      createdAt: now(),
    };
    this.ring.push(event);
    if (this.ring.length > this.ringSize) this.ring.shift();
    for (const listener of this.listeners) {
      // A broken listener must never take down the orchestrator.
      try {
        listener(event);
      } catch {
        /* ignore listener failure */
      }
    }
    return event;
  }

  /** Most recent events, newest last. */
  recent(limit = 100): JarvisEvent[] {
    return this.ring.slice(-limit);
  }

  get listenerCount(): number {
    return this.listeners.size;
  }
}
