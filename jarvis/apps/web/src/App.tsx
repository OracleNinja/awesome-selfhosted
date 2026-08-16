import { useCallback, useEffect, useState } from 'react';
import {
  api,
  subscribeToEvents,
  type Approval,
  type Conversation,
  type JarvisEvent,
  type Message,
  type SystemStatus,
} from './api';
import { ActivityFeed } from './components/ActivityFeed';
import { ApprovalCard } from './components/ApprovalCard';
import { ConversationView } from './views/ConversationView';
import { ActivityView, AgentsView, MemoryView, SettingsView, ToolsView } from './views/PanelViews';

type View = 'chat' | 'activity' | 'memory' | 'agents' | 'tools' | 'settings';
type MobilePane = 'nav' | 'main' | 'activity';

const VIEWS: { id: View; label: string }[] = [
  { id: 'chat', label: 'Conversation' },
  { id: 'activity', label: 'Activity' },
  { id: 'memory', label: 'Memory' },
  { id: 'agents', label: 'Agents' },
  { id: 'tools', label: 'Tools' },
  { id: 'settings', label: 'Settings' },
];

export default function App() {
  const [view, setView] = useState<View>('chat');
  const [mobilePane, setMobilePane] = useState<MobilePane>('main');
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [events, setEvents] = useState<JarvisEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [lastReply, setLastReply] = useState('');

  const refreshStatus = useCallback(async () => {
    try {
      const next = await api.status();
      setStatus(next);
      setConnected(true);
      setError('');
    } catch (cause) {
      setConnected(false);
      setError(
        (cause as { status?: number }).status === 401
          ? 'Not authorised. Paste your JARVIS_API_TOKEN in Settings.'
          : `Cannot reach the JARVIS server: ${(cause as Error).message}`,
      );
    }
  }, []);

  const refreshApprovals = useCallback(async () => {
    try {
      setApprovals((await api.approvals()).approvals);
    } catch {
      /* surfaced by status refresh */
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations((await api.conversations()).conversations);
    } catch {
      /* surfaced by status refresh */
    }
  }, []);

  // Initial load.
  useEffect(() => {
    void (async () => {
      await refreshStatus();
      await refreshConversations();
      await refreshApprovals();
      try {
        setEvents((await api.events(120)).events);
      } catch {
        /* ignore */
      }
    })();
  }, [refreshStatus, refreshConversations, refreshApprovals]);

  // Live activity feed.
  useEffect(() => {
    return subscribeToEvents((event) => {
      setEvents((current) => [event, ...current].slice(0, 400));
      if (event.type === 'APPROVAL_REQUEST' || event.type === 'APPROVAL_RESOLVED') {
        void refreshApprovals();
      }
    });
  }, [refreshApprovals]);

  // Load messages when the selected conversation changes.
  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    void api
      .messages(conversationId)
      .then((result) => setMessages(result.messages))
      .catch((cause: Error) => setError(cause.message));
  }, [conversationId]);

  const send = async (text: string) => {
    setBusy(true);
    setError('');
    // Optimistic echo so the message appears immediately.
    setMessages((current) => [
      ...current,
      {
        id: `local_${Date.now()}`,
        role: 'user',
        content: text,
        agent: null,
        createdAt: new Date().toISOString(),
      },
    ]);

    try {
      const turn = await api.chat(text, conversationId);
      setConversationId(turn.conversationId);
      setMessages(turn.messages);
      setLastReply(turn.reply);
      setApprovals(turn.pendingApprovals);
      if (turn.error) setError(turn.error);
      await refreshConversations();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const decide = async (id: string, decision: 'approve' | 'deny') => {
    try {
      const outcome = await (decision === 'approve' ? api.approve(id) : api.deny(id));
      if (outcome.turn) {
        setMessages(outcome.turn.messages);
        setLastReply(outcome.turn.reply);
      } else if (conversationId) {
        setMessages((await api.messages(conversationId)).messages);
      }
      if (!outcome.ok && outcome.state === 'unavailable') setError(outcome.message);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      await refreshApprovals();
    }
  };

  const newConversation = async () => {
    try {
      const created = await api.createConversation();
      setConversationId(created.conversation.id);
      setMessages([]);
      setView('chat');
      setMobilePane('main');
      await refreshConversations();
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  const currentAgent =
    [...events].find((event) => event.type === 'AGENT_DELEGATION')?.agent ?? 'jarvis';

  return (
    <div className="app" data-mobile={mobilePane}>
      <div className="brand">
        <span className={`brand-dot ${connected ? '' : 'offline'}`} />
        JARVIS
        <span className="muted small" style={{ letterSpacing: 0, marginLeft: 'auto' }}>
          {status?.version ?? ''}
        </span>
      </div>

      <div className="topbar">
        {VIEWS.map((item) => (
          <button
            key={item.id}
            className="tab"
            aria-selected={view === item.id}
            onClick={() => setView(item.id)}
          >
            {item.label}
            {item.id === 'chat' && approvals.length > 0 && ` (${approvals.length})`}
          </button>
        ))}
        <div className="row grow" style={{ justifyContent: 'flex-end', gap: 10 }}>
          <span className="small muted truncate">
            {status ? `${status.activeModelProvider} · ${status.activeModel}` : 'connecting…'}
          </span>
        </div>
      </div>

      <div className="nav">
        <div>
          <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => void newConversation()}>
            + New conversation
          </button>
        </div>

        <div>
          <div className="section-label">Views</div>
          <div className="stack" style={{ gap: 2 }}>
            {VIEWS.map((item) => (
              <button
                key={item.id}
                className="conv-item"
                aria-current={view === item.id}
                onClick={() => {
                  setView(item.id);
                  setMobilePane('main');
                }}
              >
                {item.label}
                {item.id === 'chat' && approvals.length > 0 && ` · ${approvals.length} pending`}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="section-label">Conversations</div>
          <div className="stack" style={{ gap: 2 }}>
            {conversations.length === 0 && <div className="muted small" style={{ padding: '0 8px' }}>None yet.</div>}
            {conversations.map((conversation) => (
              <button
                key={conversation.id}
                className="conv-item"
                aria-current={conversation.id === conversationId}
                onClick={() => {
                  setConversationId(conversation.id);
                  setView('chat');
                  setMobilePane('main');
                }}
                title={conversation.title}
              >
                {conversation.title}
              </button>
            ))}
          </div>
        </div>

        <div style={{ marginTop: 'auto' }}>
          <div className="section-label">System</div>
          <div className="small muted" style={{ padding: '0 8px' }}>
            {status ? (
              <>
                {status.tools.total} tools · {status.agents.length} agents
                <br />
                {status.counts.memories} memories · {status.counts.auditEvents} audit
                <br />
                db schema v{status.database.schemaVersion}
              </>
            ) : (
              'offline'
            )}
          </div>
        </div>
      </div>

      <div className="main">
        {error && <div className="error-banner">{error}</div>}
        {view === 'chat' && (
          <ConversationView
            messages={messages}
            busy={busy}
            status={status}
            onSend={send}
            lastReply={lastReply}
          />
        )}
        {view === 'activity' && <ActivityView events={events} />}
        {view === 'memory' && <MemoryView />}
        {view === 'agents' && <AgentsView conversationId={conversationId} />}
        {view === 'tools' && <ToolsView />}
        {view === 'settings' && <SettingsView status={status} onRefresh={() => void refreshStatus()} />}
      </div>

      <div className="aside">
        <div className="section-label" style={{ padding: '0 0 8px' }}>Pending approvals</div>
        {approvals.length === 0 ? (
          <div className="muted small" style={{ paddingBottom: 12 }}>Nothing waiting on you.</div>
        ) : (
          approvals.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              onApprove={(id) => decide(id, 'approve')}
              onDeny={(id) => decide(id, 'deny')}
            />
          ))
        )}

        <div className="section-label" style={{ padding: '10px 0 8px' }}>System state</div>
        <div className="card small">
          <div className="row-between"><span className="muted">Model</span><span className="mono">{status?.activeModel ?? '—'}</span></div>
          <div className="row-between"><span className="muted">Provider</span><span className="mono">{status?.activeModelProvider ?? '—'}</span></div>
          <div className="row-between"><span className="muted">Agent</span><span className="mono">{currentAgent}</span></div>
          <div className="row-between">
            <span className="muted">Status</span>
            <span className={`chip ${connected ? 'ok' : 'off'}`}>{connected ? 'online' : 'offline'}</span>
          </div>
        </div>

        <div className="section-label" style={{ padding: '10px 0 8px' }}>Live activity</div>
        <div className="card"><ActivityFeed events={events} limit={40} /></div>
      </div>

      <div className="mobile-tabs">
        <button aria-pressed={mobilePane === 'nav'} onClick={() => setMobilePane('nav')}>Menu</button>
        <button aria-pressed={mobilePane === 'main'} onClick={() => setMobilePane('main')}>
          {VIEWS.find((item) => item.id === view)?.label ?? 'Main'}
        </button>
        <button aria-pressed={mobilePane === 'activity'} onClick={() => setMobilePane('activity')}>
          Activity{approvals.length > 0 ? ` (${approvals.length})` : ''}
        </button>
      </div>
    </div>
  );
}
