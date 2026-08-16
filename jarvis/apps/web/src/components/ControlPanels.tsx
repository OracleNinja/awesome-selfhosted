/**
 * Control Room panels.
 *
 * Every panel is a projection of runtime state. None of them holds state the
 * runtime does not hold, and none of them decides anything: where a panel shows
 * "UNAVAILABLE", that is the runtime's own answer, not a local guess.
 */
import { useState } from 'react';
import { useRuntime, useRuntimeClient, shallowArrayEqual } from '../runtime/react';
import type {
  ApprovalRequest,
  MemoryActivityEntry,
  ProviderStatus,
  RuntimeAgentState,
  ToolActivityEntry,
} from '../runtime/types';

const timeOf = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

// ---------------------------------------------------------------- agents

/**
 * One agent panel.
 *
 * Scout, Operator, Advisor and Developer all render through this: the
 * differences between them (risk ceiling, tools, whether they are running) are
 * runtime facts, not per-panel code. Business metrics are deliberately absent —
 * see `ScoutIntelligence` below.
 */
export function AgentPanel({ agent }: { agent: RuntimeAgentState }) {
  const client = useRuntimeClient();
  const commandInFlight = useRuntime((state) => state.commandInFlight);
  const [task, setTask] = useState('');
  const [output, setOutput] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const run = async () => {
    if (!task.trim()) return;
    setError(null);
    setOutput(null);
    try {
      // Goes to the runtime's own agent endpoint. The Control Room does not
      // run agents; it asks the runtime to.
      const result = await client.runAgent(agent.name, task.trim());
      setOutput(result.result.output);
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  return (
    <div className={`panel agent-panel ${agent.running ? 'is-running' : ''}`} data-agent={agent.name}>
      <div className="panel-head">
        <span className="panel-title">{agent.title}</span>
        <span className={`chip ${agent.maxRisk}`}>{agent.readOnly ? 'READ-ONLY' : agent.maxRisk}</span>
      </div>

      <div className="agent-status" data-testid={`agent-status-${agent.name}`}>
        <span className={`dot ${agent.running ? 'live' : ''}`} />
        {agent.running ? 'RUNNING' : 'STANDBY'}
        <span className="muted small">
          · {agent.runCount} run{agent.runCount === 1 ? '' : 's'}
          {agent.lastRunAt ? ` · last ${timeOf(agent.lastRunAt)}` : ''}
        </span>
      </div>

      <div className="small muted agent-purpose">{agent.purpose}</div>

      <details open={open} onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
        <summary>Delegate a task ({agent.tools.length} tools)</summary>
        <textarea
          className="textarea"
          rows={2}
          placeholder="Self-contained task — the agent cannot see your conversation."
          value={task}
          onChange={(event) => setTask(event.target.value)}
        />
        <button
          className="btn btn-sm"
          disabled={commandInFlight || !task.trim()}
          onClick={() => void run()}
          data-testid={`run-${agent.name}`}
        >
          {commandInFlight ? 'Working…' : `Run ${agent.title}`}
        </button>
        {error && <div className="panel-error small">{error}</div>}
        {output && <div className="agent-output small">{output}</div>}
      </details>
    </div>
  );
}

export function AgentGrid() {
  const agents = useRuntime((state) => state.agents, shallowArrayEqual);
  if (agents.length === 0) {
    return <div className="empty small">No agents reported by the runtime.</div>;
  }
  return (
    <div className="agent-grid">
      {agents.map((agent) => (
        <AgentPanel key={agent.name} agent={agent} />
      ))}
    </div>
  );
}

/**
 * Scout's intelligence panel.
 *
 * Shows what the runtime actually knows: search availability, Scout's own
 * activity, and retrieval counts. There is no revenue, ad-spend or POS data
 * here because none is connected — and a dashboard that renders a plausible
 * number for something it cannot measure is worse than one that says so.
 */
export function ScoutIntelligence() {
  const search = useRuntime(
    (state) => state.providers.find((provider) => provider.kind === 'search') ?? null,
  );
  const counters = useRuntime((state) => state.activity?.counters ?? null);
  const scoutEvents = useRuntime(
    (state) => state.recentEvents.filter((event) => event.agent === 'scout').slice(0, 6),
    shallowArrayEqual,
  );

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Scout intelligence</span>
      </div>

      <div className="kv">
        <span className="muted">Web search</span>
        <span>
          {search?.available ? (
            <span className="chip ok">{search.id}</span>
          ) : (
            <span className="chip off" data-testid="search-unavailable">NOT CONNECTED</span>
          )}
        </span>
      </div>
      {!search?.available && search?.reason && (
        <div className="small muted panel-note">{search.reason}</div>
      )}

      <div className="kv">
        <span className="muted">Memory retrievals</span>
        <span className="mono">{counters?.memoryReads ?? '—'}</span>
      </div>
      <div className="kv">
        <span className="muted">Agent runs</span>
        <span className="mono">{counters?.agentRuns ?? '—'}</span>
      </div>

      <div className="panel-note small muted">
        Business feeds (revenue, ads, POS) are not connected in v0.2. No figures are shown for
        sources the runtime cannot read.
      </div>

      {scoutEvents.length > 0 && (
        <div className="mini-feed">
          {scoutEvents.map((event) => (
            <div className="mini-row" key={event.id}>
              <span className="event-time">{timeOf(event.createdAt)}</span>
              <span className="truncate">{event.summary}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ tools

export function ToolActivityPanel() {
  const activity = useRuntime((state) => state.toolActivity, shallowArrayEqual);

  return (
    <div className="panel" data-testid="tool-activity">
      <div className="panel-head">
        <span className="panel-title">Tool activity</span>
      </div>
      {activity.length === 0 ? (
        <div className="empty small">No tool executions this session.</div>
      ) : (
        <div className="tool-list">
          {activity.slice(0, 12).map((entry) => (
            <ToolRow key={entry.callId} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolRow({ entry }: { entry: ToolActivityEntry }) {
  const label =
    entry.status === 'running'
      ? 'STARTED'
      : entry.status === 'completed'
        ? 'COMPLETED'
        : entry.status === 'failed'
          ? 'FAILED'
          : 'AWAITING APPROVAL';

  return (
    <div className={`tool-row ${entry.status}`}>
      <span className="event-time">{timeOf(entry.startedAt)}</span>
      <span className="mono truncate">{entry.tool}</span>
      <span className="muted small">{entry.agent}</span>
      {entry.risk && <span className={`chip ${entry.risk}`}>{entry.risk}</span>}
      <span className={`tool-status ${entry.status}`}>{label}</span>
      <span className="muted small mono">
        {entry.durationMs !== null ? `${entry.durationMs}ms` : ''}
      </span>
      {entry.error && <span className="tool-error small truncate">{entry.error}</span>}
    </div>
  );
}

// -------------------------------------------------------------- approvals

export function ApprovalPanel() {
  const approvals = useRuntime((state) => state.approvals, shallowArrayEqual);

  return (
    <div className="panel approvals-panel" data-testid="approval-panel">
      <div className="panel-head">
        <span className="panel-title">Approvals</span>
        {approvals.length > 0 && <span className="chip EXTERNAL_ACTION">{approvals.length}</span>}
      </div>
      {approvals.length === 0 ? (
        <div className="empty small">Nothing waiting on you.</div>
      ) : (
        approvals.map((approval) => <RuntimeApprovalCard key={approval.id} approval={approval} />)
      )}
    </div>
  );
}

/**
 * The approval card.
 *
 * The buttons call the runtime's approval endpoints and then re-read state.
 * Nothing is marked approved because a button was pressed — the card clears
 * when the runtime says the request is resolved.
 */
export function RuntimeApprovalCard({ approval }: { approval: ApprovalRequest }) {
  const client = useRuntimeClient();
  const [busy, setBusy] = useState<'approve' | 'deny' | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const decide = async (decision: 'approve' | 'deny') => {
    setBusy(decision);
    setFailure(null);
    const outcome =
      decision === 'approve' ? await client.approve(approval.id) : await client.deny(approval.id);
    if (!outcome.ok) setFailure(outcome.message);
    setBusy(null);
  };

  return (
    <div className={`approval ${approval.risk}`} data-testid="approval-card">
      <div className="approval-title">JARVIS wants to perform:</div>
      <div className="mono small approval-action">{approval.description}</div>

      <div className="row approval-meta">
        <span className="muted small">Risk:</span>
        <span className={`chip ${approval.risk}`}>{approval.risk}</span>
        <span className="muted small">
          via {approval.agent} · {approval.tool}
        </span>
      </div>

      <div className="muted small">Arguments:</div>
      <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>

      <div className="approval-actions">
        <button
          className="btn btn-primary btn-sm"
          disabled={busy !== null}
          onClick={() => void decide('approve')}
          data-testid="approve-button"
        >
          {busy === 'approve' ? 'Executing…' : 'APPROVE'}
        </button>
        <button
          className="btn btn-danger btn-sm"
          disabled={busy !== null}
          onClick={() => void decide('deny')}
          data-testid="deny-button"
        >
          {busy === 'deny' ? 'Denying…' : 'DENY'}
        </button>
      </div>

      {failure && <div className="panel-error small">{failure}</div>}

      <div className="muted small approval-foot">
        request {approval.id} · expires {timeOf(approval.expiresAt)} · nothing runs until you decide.
      </div>
    </div>
  );
}

// -------------------------------------------------------------- providers

export function ProviderPanel() {
  const providers = useRuntime((state) => state.providers, shallowArrayEqual);
  const activeModel = useRuntime((state) => state.snapshot?.activeModel ?? null);

  return (
    <div className="panel" data-testid="provider-panel">
      <div className="panel-head">
        <span className="panel-title">Providers</span>
        {activeModel && <span className="muted small mono truncate">{activeModel.model}</span>}
      </div>
      {providers.length === 0 ? (
        <div className="empty small">Provider registry unavailable.</div>
      ) : (
        <div className="provider-list">
          {providers.map((provider) => (
            <ProviderRow
              key={`${provider.kind}:${provider.id}`}
              provider={provider}
              active={activeModel?.provider === provider.id && provider.kind === 'model'}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProviderRow({ provider, active }: { provider: ProviderStatus; active: boolean }) {
  // Three distinct states, never collapsed into two: a provider that is
  // configured but failing is a different problem from one never set up.
  const state = provider.available ? (active ? 'ACTIVE' : 'AVAILABLE') : 'NOT CONFIGURED';
  const tone = provider.available ? 'ok' : 'off';

  return (
    <div className="provider-row" title={provider.reason ?? provider.model ?? ''}>
      <span className="mono truncate">{provider.id}</span>
      <span className="muted small">{provider.kind}</span>
      <span className={`chip ${tone}`}>{state}</span>
    </div>
  );
}

// ----------------------------------------------------------------- memory

/**
 * Memory activity.
 *
 * Shows that memory is being used, not what it contains: the Memory view is
 * the place to browse memories, behind the same API and the same auth.
 */
export function MemoryPanel() {
  const activity = useRuntime((state) => state.memoryActivity, shallowArrayEqual);
  const counters = useRuntime((state) => state.activity?.counters ?? null);
  const memories = useRuntime((state) => state.snapshot?.counts.memories ?? null);

  return (
    <div className="panel" data-testid="memory-panel">
      <div className="panel-head">
        <span className="panel-title">Memory</span>
        <span className="muted small mono">{memories ?? '—'} stored</span>
      </div>

      <div className="kv">
        <span className="muted">Writes</span>
        <span className="mono">{counters?.memoryWrites ?? '—'}</span>
      </div>
      <div className="kv">
        <span className="muted">Retrievals</span>
        <span className="mono">{counters?.memoryReads ?? '—'}</span>
      </div>

      {activity.length === 0 ? (
        <div className="empty small">No memory operations this session.</div>
      ) : (
        <div className="mini-feed">
          {activity.slice(0, 8).map((entry) => (
            <MemoryRow key={entry.id} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

function MemoryRow({ entry }: { entry: MemoryActivityEntry }) {
  const label =
    entry.kind === 'write' ? 'MEMORY WRITE' : entry.kind === 'search' ? 'MEMORY SEARCH' : 'MEMORY RETRIEVAL';
  return (
    <div className="mini-row">
      <span className="event-time">{timeOf(entry.at)}</span>
      <span className={`memory-kind ${entry.kind}`}>{label}</span>
      <span className="truncate muted">{entry.summary}</span>
    </div>
  );
}

// -------------------------------------------------------------- telemetry

const formatBytes = (bytes: number | null): string => {
  if (bytes === null) return 'UNAVAILABLE';
  const mb = bytes / 1024 / 1024;
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`;
};

export function formatUptime(seconds: number): string {
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

/** Measured values only. A metric the platform cannot supply says UNAVAILABLE. */
export function TelemetryPanel() {
  const telemetry = useRuntime((state) => state.telemetry);
  const counters = useRuntime((state) => state.activity?.counters ?? null);
  const load = useRuntime((state) => state.activity?.load ?? 0);

  if (!telemetry) {
    return (
      <div className="panel" data-testid="telemetry-panel">
        <div className="panel-head">
          <span className="panel-title">Telemetry</span>
        </div>
        <div className="empty small">Runtime telemetry unavailable — not connected.</div>
      </div>
    );
  }

  return (
    <div className="panel" data-testid="telemetry-panel">
      <div className="panel-head">
        <span className="panel-title">Telemetry</span>
        <span className="muted small mono">{telemetry.platform}</span>
      </div>

      <div className="kv">
        <span className="muted">Uptime</span>
        <span className="mono" data-testid="telemetry-uptime">
          {formatUptime(telemetry.uptimeSeconds)}
        </span>
      </div>
      <div className="kv">
        <span className="muted">Process memory</span>
        <span className="mono">{formatBytes(telemetry.memory.processRssBytes)}</span>
      </div>
      <div className="kv">
        <span className="muted">Host memory</span>
        <span className="mono">
          {telemetry.memory.systemUsedFraction === null
            ? 'UNAVAILABLE'
            : `${Math.round(telemetry.memory.systemUsedFraction * 100)}% used`}
        </span>
      </div>
      <div className="kv">
        <span className="muted">Load (1m / core)</span>
        <span className="mono">
          {telemetry.cpu.loadPerCore === null
            ? 'UNAVAILABLE'
            : telemetry.cpu.loadPerCore.toFixed(2)}
        </span>
      </div>
      <div className="kv">
        <span className="muted">Process CPU</span>
        <span className="mono">
          {telemetry.cpu.processCpuFraction === null
            ? 'SAMPLING…'
            : `${Math.round(telemetry.cpu.processCpuFraction * 100)}%`}
        </span>
      </div>
      <div className="kv">
        <span className="muted">Runtime load</span>
        <span className="mono">{Math.round(load * 100)}%</span>
      </div>

      {counters && (
        <div className="counter-grid small">
          <span>events <b className="mono">{counters.events}</b></span>
          <span>tools <b className="mono">{counters.toolCalls}</b></span>
          <span>failures <b className="mono">{counters.toolFailures}</b></span>
          <span>errors <b className="mono">{counters.errors}</b></span>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------ event stream

export function RuntimeEventStream({ limit = 40 }: { limit?: number }) {
  const events = useRuntime((state) => state.recentEvents.slice(0, limit), shallowArrayEqual);
  const dropped = useRuntime((state) => state.droppedEvents);

  return (
    <div className="panel event-panel" data-testid="event-stream">
      <div className="panel-head">
        <span className="panel-title">Event stream</span>
        {dropped > 0 && (
          <span className="chip off" title="Frames that failed validation and were discarded">
            {dropped} dropped
          </span>
        )}
      </div>
      {events.length === 0 ? (
        <div className="empty small">Waiting for runtime events…</div>
      ) : (
        <div className="event-list">
          {events.map((event) => (
            <div className="event" key={event.id}>
              <span className="event-time">{timeOf(event.createdAt)}</span>
              <span className={`event-kind ${event.kind.split('.')[0]}`}>{event.kind}</span>
              <span className="event-summary">{event.summary}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
