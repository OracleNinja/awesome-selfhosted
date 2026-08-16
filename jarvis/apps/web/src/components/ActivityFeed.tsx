import type { JarvisEvent } from '../api';

const SHORT_TYPE: Record<string, string> = {
  USER_MESSAGE: 'USER',
  MODEL_RESPONSE: 'MODEL',
  TOOL_REQUEST: 'TOOL→',
  TOOL_RESULT: 'TOOL←',
  MEMORY_WRITE: 'MEM+',
  MEMORY_READ: 'MEM?',
  AGENT_DELEGATION: 'AGENT→',
  AGENT_RESULT: 'AGENT←',
  APPROVAL_REQUEST: 'APPR?',
  APPROVAL_RESOLVED: 'APPR!',
  ACTION_EXECUTED: 'EXEC',
  ERROR: 'ERROR',
};

export function ActivityFeed({ events, limit = 60 }: { events: JarvisEvent[]; limit?: number }) {
  if (events.length === 0) {
    return <div className="empty">No activity yet. Send a message to JARVIS.</div>;
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
          <span className={`event-type ${event.type}`}>{SHORT_TYPE[event.type] ?? event.type}</span>
          <span className="event-summary">{event.summary}</span>
        </div>
      ))}
    </div>
  );
}
