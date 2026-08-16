/**
 * Application shell.
 *
 * Creates the one runtime client, provides it, and switches views. It holds no
 * runtime state: conversation selection and which tab is open are UI concerns,
 * everything else lives in the runtime store and is written there by the client.
 */
import { useEffect, useMemo, useState } from 'react';
import { JarvisRuntimeClient } from './runtime/client';
import { RuntimeContext, useRuntime } from './runtime/react';
import { TopBar } from './components/TopBar';
import { ControlRoom } from './views/ControlRoom';
import { ConversationView } from './views/ConversationView';
import { ActivityView, AgentsView, MemoryView, SettingsView, ToolsView } from './views/PanelViews';
import { api, type Conversation, type Message } from './runtime/api';

type View = 'control' | 'chat' | 'activity' | 'memory' | 'agents' | 'tools' | 'settings';

const VIEWS: { id: View; label: string }[] = [
  { id: 'control', label: 'Control Room' },
  { id: 'chat', label: 'Conversation' },
  { id: 'activity', label: 'Activity' },
  { id: 'memory', label: 'Memory' },
  { id: 'agents', label: 'Agents' },
  { id: 'tools', label: 'Tools' },
  { id: 'settings', label: 'Settings' },
];

export default function App() {
  // One client for the lifetime of the app — one SSE connection, not one per
  // component.
  const client = useMemo(() => new JarvisRuntimeClient(), []);

  useEffect(() => {
    void client.connect();
    return () => client.disconnect();
  }, [client]);

  return (
    <RuntimeContext.Provider value={client}>
      <Shell />
    </RuntimeContext.Provider>
  );
}

function Shell() {
  const [view, setView] = useState<View>('control');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [error, setError] = useState('');

  const pendingApprovals = useRuntime((state) => state.approvals.length);
  const connection = useRuntime((state) => state.connection);

  const refreshConversations = async () => {
    try {
      setConversations((await api.conversations()).conversations);
    } catch {
      /* connection state already reflects this */
    }
  };

  useEffect(() => {
    if (connection === 'online') void refreshConversations();
  }, [connection]);

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

  // The Control Room and the Conversation view share a conversation; when one
  // advances it, the other reloads the transcript.
  const adoptConversation = (id: string) => {
    setConversationId(id);
    void api
      .messages(id)
      .then((result) => setMessages(result.messages))
      .catch(() => undefined);
    void refreshConversations();
  };

  const newConversation = async () => {
    try {
      const created = await api.createConversation();
      setConversationId(created.conversation.id);
      setMessages([]);
      await refreshConversations();
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  return (
    <div className="app-cr">
      <TopBar />

      <nav className="viewbar" data-testid="view-bar">
        {VIEWS.map((item) => (
          <button
            key={item.id}
            className="tab"
            aria-selected={view === item.id}
            onClick={() => setView(item.id)}
            data-testid={`view-${item.id}`}
          >
            {item.label}
            {item.id === 'control' && pendingApprovals > 0 && ` (${pendingApprovals})`}
          </button>
        ))}
        <span className="grow" />
        <button className="btn btn-sm" onClick={() => void newConversation()}>
          + Conversation
        </button>
      </nav>

      {error && <div className="error-banner">{error}</div>}

      <main className="viewport">
        {view === 'control' && (
          <ControlRoom conversationId={conversationId} onConversation={adoptConversation} />
        )}
        {view === 'chat' && (
          <ConversationPane
            conversationId={conversationId}
            conversations={conversations}
            messages={messages}
            onSelect={adoptConversation}
            onMessages={setMessages}
          />
        )}
        {view === 'activity' && <ActivityView />}
        {view === 'memory' && <MemoryView />}
        {view === 'agents' && <AgentsView conversationId={conversationId} />}
        {view === 'tools' && <ToolsView />}
        {view === 'settings' && <SettingsView />}
      </main>
    </div>
  );
}

function ConversationPane({
  conversationId,
  conversations,
  messages,
  onSelect,
  onMessages,
}: {
  conversationId: string | null;
  conversations: Conversation[];
  messages: Message[];
  onSelect: (id: string) => void;
  onMessages: (messages: Message[]) => void;
}) {
  return (
    <div className="conversation-pane">
      <aside className="conv-list">
        <div className="section-label">Conversations</div>
        {conversations.length === 0 && <div className="muted small conv-empty">None yet.</div>}
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            className="conv-item"
            aria-current={conversation.id === conversationId}
            onClick={() => onSelect(conversation.id)}
            title={conversation.title}
          >
            {conversation.title}
          </button>
        ))}
      </aside>
      <div className="conv-main">
        <ConversationView
          conversationId={conversationId}
          messages={messages}
          onMessages={onMessages}
          onConversation={onSelect}
        />
      </div>
    </div>
  );
}
