import type { RuntimeEvent } from '../runtime/types';

/**
 * Compact activity feed over normalised runtime events.
 * The semantic `kind` is what is shown — the backend event name is preserved
 * in the payload for anyone reading the raw stream.
 */
export function ActivityFeed({ events, limit = 60 }: { events: RuntimeEvent[]; limit?: number }) {
  if (events.length === 0) {
    return <div className="empty">No activity yet. Send a command to JARVIS.</div>;
  }
  return (
    <div>
      {events.slice(0, limit).map((event) => (
        <div className="event" key={event.id}>
          <span className="event-time">
            {new Date(event.createdAt).toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
            })}
          </span>
          <span className={`event-kind ${event.kind.split('.')[0]}`}>{event.kind}</span>
          <span className="event-summary">{event.summary}</span>
        </div>
      ))}
    </div>
  );
}
