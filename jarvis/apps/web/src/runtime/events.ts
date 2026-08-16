/**
 * Event normalisation.
 *
 * The brief lists semantic event names (`tool.started`, `approval.expired`).
 * The runtime emits its own names (`TOOL_REQUEST`, `APPROVAL_RESOLVED` with a
 * decision). The backend naming is authoritative and is left alone; this module
 * derives the semantic label for display, in one place, so no component has to
 * know that "approval expired" is an `APPROVAL_RESOLVED` with
 * `data.decision === 'expired'`.
 *
 * This is translation, not logic: nothing here decides anything, and the
 * original event is carried through untouched.
 */
import type { JarvisEvent, RuntimeEvent, RuntimeEventKind } from './types';

const KNOWN_TYPES = new Set([
  'USER_MESSAGE',
  'MODEL_RESPONSE',
  'TOOL_REQUEST',
  'TOOL_RESULT',
  'MEMORY_WRITE',
  'MEMORY_READ',
  'AGENT_DELEGATION',
  'AGENT_RESULT',
  'APPROVAL_REQUEST',
  'APPROVAL_RESOLVED',
  'ACTION_EXECUTED',
  'ERROR',
]);

/** Backend event type (+ payload) → semantic kind. */
export function classify(event: JarvisEvent): RuntimeEventKind {
  switch (event.type) {
    case 'USER_MESSAGE':
    case 'MODEL_RESPONSE':
    case 'ACTION_EXECUTED':
      return 'orchestration';

    case 'AGENT_DELEGATION':
      return 'agent.started';

    case 'AGENT_RESULT':
      return 'agent.completed';

    case 'TOOL_REQUEST':
      return 'tool.requested';

    case 'TOOL_RESULT':
      return event.data.ok === false ? 'tool.failed' : 'tool.completed';

    case 'APPROVAL_REQUEST':
      return 'approval.requested';

    case 'APPROVAL_RESOLVED': {
      const decision = event.data.decision;
      if (decision === 'approved') return 'approval.approved';
      if (decision === 'denied') return 'approval.denied';
      if (decision === 'expired') return 'approval.expired';
      return 'orchestration';
    }

    case 'MEMORY_WRITE':
      return 'memory.write';

    case 'MEMORY_READ':
      return event.data.via === 'memory_search' ? 'memory.search' : 'memory.read';

    case 'ERROR':
      // The executor tags provider failures; an agent-scoped error is an agent
      // failure. Anything else stays a generic error rather than being guessed at.
      if (event.data.kind === 'provider') return 'provider.error';
      if (event.agent && event.agent !== 'jarvis' && event.agent !== 'user') return 'agent.failed';
      return 'error';

    default:
      return 'unknown';
  }
}

export function isKnownEventType(type: string): boolean {
  return KNOWN_TYPES.has(type);
}

/**
 * Validate and normalise a frame from the stream.
 *
 * Returns null for anything malformed. A client that trusts the shape of every
 * frame it is handed will throw inside an EventSource callback, which silently
 * kills the subscription — so this is deliberately defensive about a payload
 * that has already crossed a process boundary.
 */
export function normalizeEvent(raw: unknown, receivedAt = Date.now()): RuntimeEvent | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const candidate = raw as Partial<JarvisEvent>;

  if (typeof candidate.id !== 'string' || typeof candidate.type !== 'string') return null;
  if (typeof candidate.summary !== 'string') return null;

  const createdAt =
    typeof candidate.createdAt === 'string' && !Number.isNaN(Date.parse(candidate.createdAt))
      ? candidate.createdAt
      : new Date(receivedAt).toISOString();

  const data =
    candidate.data && typeof candidate.data === 'object' && !Array.isArray(candidate.data)
      ? (candidate.data as Record<string, unknown>)
      : {};

  const event: JarvisEvent = {
    id: candidate.id,
    // An unknown type is kept verbatim rather than dropped: a newer runtime
    // must not make an older client blind, it should just show it plainly.
    type: candidate.type as JarvisEvent['type'],
    conversationId: typeof candidate.conversationId === 'string' ? candidate.conversationId : null,
    userId: typeof candidate.userId === 'string' ? candidate.userId : '',
    agent: typeof candidate.agent === 'string' ? candidate.agent : 'unknown',
    summary: candidate.summary,
    data,
    createdAt,
  };

  return {
    id: event.id,
    type: event.type,
    kind: isKnownEventType(event.type) ? classify(event) : 'unknown',
    agent: event.agent,
    summary: event.summary,
    conversationId: event.conversationId,
    data: event.data,
    createdAt,
    receivedAt,
  };
}

/** Events older than this are stale on arrival — shown, but not treated as live. */
export const STALE_EVENT_MS = 5 * 60 * 1000;

export function isStale(event: RuntimeEvent, reference = Date.now()): boolean {
  const created = Date.parse(event.createdAt);
  if (Number.isNaN(created)) return false;
  return reference - created > STALE_EVENT_MS;
}
