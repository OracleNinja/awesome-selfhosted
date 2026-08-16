/**
 * The non-conversation views: Activity, Memory, Agents, Tools, Settings.
 *
 * Each is a plain function component over data fetched by App — no client-side
 * router and no state library, because there is not enough state here to earn
 * either.
 */
import { useEffect, useState } from 'react';
import {
  api,
  getToken,
  setToken,
  type AuditEntry,
  type CapabilityReport,
  type Memory,
} from '../runtime/api';
import { useRuntime, useRuntimeClient, shallowArrayEqual } from '../runtime/react';
import { ActivityFeed } from '../components/ActivityFeed';

// --------------------------------------------------------------- Activity

export function ActivityView() {
  const events = useRuntime((state) => state.recentEvents, shallowArrayEqual);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [filter, setFilter] = useState<string>('all');
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .audit(150)
      .then((result) => setAudit(result.events))
      .catch((cause: Error) => setError(cause.message));
  }, [events.length]);

  const agents = ['all', 'jarvis', 'scout', 'operator', 'advisor', 'developer'];
  const filtered = filter === 'all' ? events : events.filter((event) => event.agent === filter);

  return (
    <div className="scroll pad grow">
      <div className="row-between" style={{ marginBottom: 10 }}>
        <div className="section-label" style={{ padding: 0 }}>Event stream</div>
        <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
          {agents.map((agent) => (
            <button
              key={agent}
              className="btn btn-sm"
              aria-pressed={filter === agent}
              style={filter === agent ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
              onClick={() => setFilter(agent)}
            >
              {agent}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      <div className="card"><ActivityFeed events={filtered} limit={120} /></div>

      <div className="section-label" style={{ padding: '14px 0 6px' }}>
        Audit log — every tool invocation, including refusals
      </div>
      <div className="card table-wrap">
        {audit.length === 0 ? (
          <div className="empty">No tool calls recorded yet.</div>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Time</th>
                <th>Agent</th>
                <th>Tool</th>
                <th>Risk</th>
                <th>Approval</th>
                <th>Outcome</th>
                <th>ms</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((entry) => (
                <tr key={entry.id}>
                  <td className="mono">{new Date(entry.timestamp).toLocaleTimeString()}</td>
                  <td>{entry.agent}</td>
                  <td className="mono">{entry.tool}</td>
                  <td><span className={`chip ${entry.risk}`}>{entry.risk}</span></td>
                  <td className="small muted">{entry.approvalState}</td>
                  <td className="small" style={{ maxWidth: 320 }}>
                    {entry.error ? (
                      <span style={{ color: 'var(--danger)' }}>{entry.error}</span>
                    ) : (
                      <span className="muted">{truncate(entry.result ?? '', 120)}</span>
                    )}
                  </td>
                  <td className="mono">{entry.durationMs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------- Memory

export function MemoryView() {
  const [memories, setMemories] = useState<(Memory & { score?: number })[]>([]);
  const [query, setQuery] = useState('');
  const [type, setType] = useState('');
  const [content, setContent] = useState('');
  const [newType, setNewType] = useState('fact');
  const [error, setError] = useState('');

  const load = () => {
    api
      .memories(query || undefined, type || undefined)
      .then((result) => setMemories(result.memories))
      .catch((cause: Error) => setError(cause.message));
  };

  useEffect(load, [query, type]);

  const add = async () => {
    if (!content.trim()) return;
    try {
      await api.createMemory({ type: newType, content: content.trim(), importance: 0.7 });
      setContent('');
      load();
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  const remove = async (id: string) => {
    await api.deleteMemory(id);
    load();
  };

  const types = ['preference', 'fact', 'goal', 'project', 'person', 'instruction', 'temporary'];

  return (
    <div className="scroll pad grow">
      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        <div className="section-label" style={{ padding: '0 0 8px' }}>Add a memory</div>
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <select className="select" style={{ width: 150 }} value={newType} onChange={(e) => setNewType(e.target.value)}>
            {types.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
          <input
            className="input grow"
            style={{ minWidth: 200 }}
            placeholder="Something JARVIS should remember…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void add()}
          />
          <button className="btn btn-primary" onClick={() => void add()} disabled={!content.trim()}>
            Save
          </button>
        </div>
      </div>

      <div className="row" style={{ gap: 8, margin: '4px 0 12px', flexWrap: 'wrap' }}>
        <input
          className="input grow"
          style={{ minWidth: 200 }}
          placeholder="Search memory…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select className="select" style={{ width: 160 }} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">all types</option>
          {types.map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      </div>

      {memories.length === 0 ? (
        <div className="empty">
          {query ? 'No memories match that search.' : 'Memory is empty. JARVIS saves things when you tell it to.'}
        </div>
      ) : (
        <div className="grid-cards">
          {memories.map((memory) => (
            <div className="card" key={memory.id}>
              <div className="row-between" style={{ marginBottom: 6 }}>
                <span className="chip">{memory.type}</span>
                <button className="btn btn-sm btn-danger" onClick={() => void remove(memory.id)}>
                  Forget
                </button>
              </div>
              <div style={{ marginBottom: 8 }}>{memory.content}</div>
              <div className="small muted">
                importance {memory.importance.toFixed(2)} · confidence {memory.confidence.toFixed(2)}
                {memory.score !== undefined && ` · match ${memory.score.toFixed(2)}`}
                <br />
                {memory.source} · {new Date(memory.updatedAt).toLocaleString()}
                {memory.expiresAt && ` · expires ${new Date(memory.expiresAt).toLocaleString()}`}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------- Agents

export function AgentsView({ conversationId }: { conversationId: string | null }) {
  const client = useRuntimeClient();
  const agents = useRuntime((state) => state.agents, shallowArrayEqual);
  const [task, setTask] = useState('');
  const [running, setRunning] = useState<string | null>(null);
  const [output, setOutput] = useState<{ agent: string; text: string } | null>(null);
  const [error, setError] = useState('');

  const run = async (name: string) => {
    if (!task.trim()) {
      setError('Give the agent a task first.');
      return;
    }
    setError('');
    setRunning(name);
    setOutput(null);
    try {
      const result = await client.runAgent(name, task.trim(), conversationId);
      setOutput({ agent: name, text: result.result.output });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setRunning(null);
    }
  };

  return (
    <div className="scroll pad grow">
      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        <div className="section-label" style={{ padding: '0 0 8px' }}>Delegate a task directly</div>
        <textarea
          className="textarea"
          rows={3}
          placeholder="Describe a self-contained task. The agent cannot see your conversation."
          value={task}
          onChange={(e) => setTask(e.target.value)}
        />
      </div>

      <div className="grid-cards">
        {agents.map((agent) => (
          <div className="card" key={agent.name}>
            <div className="row-between" style={{ marginBottom: 6 }}>
              <strong>{agent.title}</strong>
              <span className={`chip ${agent.maxRisk}`}>{agent.readOnly ? 'READ-ONLY' : agent.maxRisk}</span>
            </div>
            <div className="small" style={{ marginBottom: 8 }}>{agent.purpose}</div>
            <div className="small muted" style={{ marginBottom: 8 }}>
              {agent.tools.length} tools · max {agent.maxIterations} steps · {agent.runCount} run
              {agent.runCount === 1 ? '' : 's'}
              {agent.lastRunAt && ` · last ${new Date(agent.lastRunAt).toLocaleString()}`}
            </div>
            <details style={{ marginBottom: 10 }}>
              <summary>Permitted tools</summary>
              <div className="mono small" style={{ marginTop: 6 }}>{agent.tools.join(', ')}</div>
            </details>
            <button
              className="btn btn-sm"
              disabled={running !== null}
              onClick={() => void run(agent.name)}
            >
              {running === agent.name ? 'Running…' : `Run ${agent.title}`}
            </button>
          </div>
        ))}
      </div>

      {output && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="section-label" style={{ padding: '0 0 8px' }}>{output.agent} report</div>
          <div style={{ whiteSpace: 'pre-wrap' }}>{output.text}</div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Tools

export function ToolsView() {
  const [report, setReport] = useState<CapabilityReport | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .capabilities()
      .then(setReport)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  const tools = report?.capabilities ?? [];
  const levels = report?.approvalRequiredLevels ?? [];
  const servers = report?.mcpServers ?? [];

  return (
    <div className="scroll pad grow">
      {error && <div className="error-banner">{error}</div>}
      <div className="small muted" style={{ marginBottom: 12 }}>
        {report?.counts.total ?? 0} capabilities registered
        {report ? ` · ${report.counts.local} local · ${report.counts.remote} remote` : ''}
        {report && report.counts.enabled !== report.counts.total
          ? ` · ${report.counts.total - report.counts.enabled} unavailable`
          : ''}
        . Actions at{' '}
        {levels.map((level) => (
          <span className={`chip ${level}`} key={level} style={{ marginRight: 4 }}>{level}</span>
        ))}
        require your approval before they run.
      </div>

      {/* Where remote capabilities come from, and what authority they were given. */}
      {servers.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="section-label" style={{ padding: '0 0 8px' }}>MCP servers</div>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Remote servers provide capabilities. They do not classify them: the risk floor
            below is set in configuration, and a server&rsquo;s own claims about its safety
            are ignored.
          </div>
          {servers.map((server) => (
            <div className="row-between" key={server.id} style={{ marginBottom: 8 }}>
              <div>
                <span className="mono" style={{ fontSize: 13 }}>{server.id}</span>
                <div className="small muted">{server.detail}</div>
                {server.rejected.map((rejected) => (
                  <div className="small muted" key={rejected.name}>
                    refused &ldquo;{rejected.name}&rdquo;: {rejected.reason}
                  </div>
                ))}
              </div>
              <div className="row" style={{ gap: 6 }}>
                <span className={`chip ${server.riskFloor}`}>floor {server.riskFloor}</span>
                <span className={`chip ${server.state === 'ready' ? 'ok' : 'off'}`}>{server.state}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Routing is on declared model capability, never on a guess about the question. */}
      {report && report.models.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="section-label" style={{ padding: '0 0 8px' }}>Model routing</div>
          {report.models.map((entry) => (
            <div className="row-between" key={entry.providerId} style={{ marginBottom: 6 }}>
              <div>
                <span className="mono" style={{ fontSize: 13 }}>{entry.providerId}</span>
                {entry.isDefault && <span className="chip ok" style={{ marginLeft: 6 }}>default</span>}
                <div className="small muted">
                  {entry.model || 'no model configured'}
                  {entry.available
                    ? ''
                    : ` · ${entry.reason ?? 'not configured'}`}
                </div>
              </div>
              <div className="small mono muted">
                {Object.entries(entry.capabilities)
                  .filter(([, enabled]) => enabled)
                  .map(([name]) => name)
                  .join(' ') || 'none declared'}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid-cards">
        {tools.map((tool) => (
          <div className="card" key={tool.name}>
            <div className="row-between" style={{ marginBottom: 6 }}>
              <span className="mono" style={{ fontSize: 13 }}>{tool.name}</span>
              <span className={`chip ${tool.risk}`}>{tool.risk}</span>
            </div>
            <div className="small" style={{ marginBottom: 8 }}>{tool.description}</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <span className="chip">
                {tool.source.kind === 'local' ? 'local' : `via ${tool.source.server}`}
              </span>
              {tool.requiresApproval && <span className="chip EXTERNAL_ACTION">approval required</span>}
              <span className={`chip ${tool.available && tool.enabled ? 'ok' : 'off'}`}>
                {tool.available && tool.enabled ? 'available' : 'unavailable'}
              </span>
            </div>
            {(!tool.available || !tool.enabled) && tool.unavailableReason && (
              <div className="small muted" style={{ marginTop: 8 }}>{tool.unavailableReason}</div>
            )}
            {tool.inputSchema.properties && (
              <details style={{ marginTop: 8 }}>
                <summary>Parameters</summary>
                <div className="mono small" style={{ marginTop: 6 }}>
                  {Object.entries(tool.inputSchema.properties).map(([name, schema]) => (
                    <div key={name}>
                      {name}
                      {tool.inputSchema.required?.includes(name) ? '*' : ''}: {schema.type ?? 'any'}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------- Settings

export function SettingsView() {
  const client = useRuntimeClient();
  const status = useRuntime((state) => state.snapshot);
  const [token, setTokenState] = useState(getToken());
  const [check, setCheck] = useState<{ ok: boolean; detail: string } | null>(null);
  const [checking, setChecking] = useState(false);
  const [saved, setSaved] = useState(false);

  const runCheck = async () => {
    setChecking(true);
    setCheck(null);
    try {
      const result = await api.providerCheck();
      setCheck({ ok: result.ok, detail: result.detail });
    } catch (cause) {
      setCheck({ ok: false, detail: (cause as Error).message });
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="scroll pad grow">
      <div className="card">
        <div className="section-label" style={{ padding: '0 0 8px' }}>Model provider</div>
        {status ? (
          <>
            <div className="row" style={{ gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
              <span className={`chip ${status.activeModel.available ? 'ok' : 'off'}`}>{status.activeModel.provider}</span>
              <span className="mono small">{status.activeModel.model}</span>
            </div>
            <button className="btn btn-sm" onClick={() => void runCheck()} disabled={checking}>
              {checking ? 'Contacting provider…' : 'Test live connection'}
            </button>
            {check && (
              <div className="small" style={{ marginTop: 8, color: check.ok ? 'var(--ok)' : 'var(--danger)' }}>
                {check.ok ? '✓ ' : '✕ '}
                {check.detail}
              </div>
            )}
            <div className="small muted" style={{ marginTop: 10 }}>
              Change providers in <span className="mono">.env</span> (MODEL_PROVIDER=nvidia|anthropic|openai|local)
              and restart the server. Keys are read server-side only and are never sent to this page.
            </div>
          </>
        ) : (
          <div className="muted">Loading…</div>
        )}
      </div>

      <div className="card">
        <div className="section-label" style={{ padding: '0 0 8px' }}>Capabilities</div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th>Provider</th><th>Kind</th><th>State</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {(status?.providers ?? []).map((provider) => (
                <tr key={`${provider.kind}:${provider.id}`}>
                  <td className="mono">{provider.id}</td>
                  <td className="small muted">{provider.kind}</td>
                  <td>
                    <span className={`chip ${provider.available ? 'ok' : 'off'}`}>
                      {provider.available ? 'ready' : 'not configured'}
                    </span>
                  </td>
                  <td className="small muted">{provider.reason ?? provider.model ?? provider.endpoint ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="section-label" style={{ padding: '0 0 8px' }}>API token</div>
        <div className="small muted" style={{ marginBottom: 8 }}>
          Needed only when the server runs with JARVIS_API_TOKEN set. Stored in this browser only.
        </div>
        <div className="row" style={{ gap: 8 }}>
          <input
            className="input grow"
            type="password"
            placeholder="paste JARVIS_API_TOKEN"
            value={token}
            onChange={(e) => {
              setTokenState(e.target.value);
              setSaved(false);
            }}
          />
          <button
            className="btn"
            onClick={() => {
              setToken(token);
              setSaved(true);
              void client.refreshState();
            }}
          >
            {saved ? 'Saved' : 'Save'}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="section-label" style={{ padding: '0 0 8px' }}>System</div>
        <div className="small mono">
          version {status?.version ?? '—'} · schema v{status?.database.schemaVersion ?? '—'} ·{' '}
          {status?.database.tables.length ?? 0} tables · {status?.tools.total ?? 0} tools ·{' '}
          {status?.counts.memories ?? 0} memories · {status?.counts.auditEvents ?? 0} audit records
        </div>
        {status?.charterErrors && status.charterErrors.length > 0 && (
          <div className="error-banner" style={{ margin: '10px 0 0' }}>
            Agent charter problems: {status.charterErrors.join('; ')}
          </div>
        )}
      </div>
    </div>
  );
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
