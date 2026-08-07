import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Sidebar from './components/Sidebar';
import MessageList from './components/MessageList';
import Composer from './components/Composer';
import StatusBar from './components/StatusBar';
import { bridge } from './lib/bridge';
import {
  loadConversations,
  loadPreferredModel,
  saveConversations,
  savePreferredModel,
} from './lib/storage';
import {
  createId,
  DEFAULT_MODEL,
  type ChatMessage,
  type Conversation,
  type StatusResult,
} from './types';

/** Which message a live stream is writing into. */
interface StreamTarget {
  requestId: string;
  conversationId: string;
  messageId: string;
}

function titleFrom(text: string): string {
  const cleaned = text.replace(/\s+/g, ' ').trim();
  if (!cleaned) return 'New chat';
  return cleaned.length > 42 ? `${cleaned.slice(0, 42)}…` : cleaned;
}

function newConversation(): Conversation {
  const now = Date.now();
  return { id: createId(), title: 'New chat', messages: [], createdAt: now, updatedAt: now };
}

export default function App() {
  const restored = useMemo(() => loadConversations(), []);

  const [conversations, setConversations] = useState<Conversation[]>(restored);
  const [activeId, setActiveId] = useState<string | null>(restored[0]?.id ?? null);
  const [model, setModel] = useState<string>(() => loadPreferredModel() ?? DEFAULT_MODEL);
  const [status, setStatus] = useState<StatusResult | null>(null);
  const [streaming, setStreaming] = useState<StreamTarget | null>(null);

  // The IPC listeners are registered once; they read the live target from a ref
  // so they never close over a stale value.
  const streamRef = useRef<StreamTarget | null>(null);
  streamRef.current = streaming;

  const activeConversation = conversations.find((c) => c.id === activeId) ?? null;
  const isStreaming = streaming !== null;

  const patchMessage = useCallback(
    (conversationId: string, messageId: string, patch: (m: ChatMessage) => ChatMessage) => {
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== conversationId
            ? c
            : {
                ...c,
                updatedAt: Date.now(),
                messages: c.messages.map((m) => (m.id === messageId ? patch(m) : m)),
              },
        ),
      );
    },
    [],
  );

  // --- Ollama connection status -------------------------------------------

  const refreshStatus = useCallback(async () => {
    const next = await bridge.getStatus();
    setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    void refreshStatus().then((next) => {
      if (!next.connected || next.models.length === 0) return;
      // Prefer the saved choice, then ornith-en, then whatever is installed.
      setModel((current) => {
        if (next.models.includes(current)) return current;
        if (next.models.includes(DEFAULT_MODEL)) return DEFAULT_MODEL;
        const prefixMatch = next.models.find((m) => m.startsWith(`${DEFAULT_MODEL}:`));
        return prefixMatch ?? next.models[0];
      });
    });

    // Ollama unloads models when idle and can be restarted underneath us.
    const timer = setInterval(() => void refreshStatus(), 15_000);
    return () => clearInterval(timer);
  }, [refreshStatus]);

  useEffect(() => savePreferredModel(model), [model]);

  // --- Streaming wiring ----------------------------------------------------

  useEffect(() => {
    const offDelta = bridge.onDelta(({ requestId, text }) => {
      const target = streamRef.current;
      if (!target || target.requestId !== requestId) return;
      patchMessage(target.conversationId, target.messageId, (m) => ({
        ...m,
        content: m.content + text,
      }));
    });

    const offDone = bridge.onDone(({ requestId, stats }) => {
      const target = streamRef.current;
      if (!target || target.requestId !== requestId) return;

      const tokensPerSecond =
        stats.evalDurationNs > 0 && stats.evalCount > 0
          ? stats.evalCount / (stats.evalDurationNs / 1e9)
          : undefined;

      patchMessage(target.conversationId, target.messageId, (m) => ({ ...m, tokensPerSecond }));
      setStreaming(null);
    });

    const offError = bridge.onError(({ requestId, message }) => {
      const target = streamRef.current;
      if (!target || target.requestId !== requestId) return;
      patchMessage(target.conversationId, target.messageId, (m) => ({ ...m, error: message }));
      setStreaming(null);
      void refreshStatus();
    });

    return () => {
      offDelta();
      offDone();
      offError();
    };
  }, [patchMessage, refreshStatus]);

  // --- Persistence ---------------------------------------------------------

  useEffect(() => {
    // Debounced: streaming would otherwise write to localStorage on every token.
    const timer = setTimeout(() => saveConversations(conversations), 400);
    return () => clearTimeout(timer);
  }, [conversations]);

  // --- Actions -------------------------------------------------------------

  const handleNewChat = useCallback(() => {
    if (streamRef.current) bridge.abortChat(streamRef.current.requestId);
    setStreaming(null);

    setConversations((prev) => {
      // Reuse an existing empty chat rather than stacking up blanks.
      const empty = prev.find((c) => c.messages.length === 0);
      if (empty) {
        setActiveId(empty.id);
        return prev;
      }
      const created = newConversation();
      setActiveId(created.id);
      return [created, ...prev];
    });
  }, []);

  const handleSelect = useCallback((id: string) => {
    if (streamRef.current) bridge.abortChat(streamRef.current.requestId);
    setStreaming(null);
    setActiveId(id);
  }, []);

  const handleDelete = useCallback((id: string) => {
    if (streamRef.current?.conversationId === id) {
      bridge.abortChat(streamRef.current.requestId);
      setStreaming(null);
    }
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      setActiveId((current) => (current === id ? (next[0]?.id ?? null) : current));
      return next;
    });
  }, []);

  const handleClear = useCallback(() => {
    const target = activeId;
    if (!target) return;
    if (streamRef.current?.conversationId === target) {
      bridge.abortChat(streamRef.current.requestId);
      setStreaming(null);
    }
    setConversations((prev) =>
      prev.map((c) =>
        c.id === target ? { ...c, messages: [], title: 'New chat', updatedAt: Date.now() } : c,
      ),
    );
  }, [activeId]);

  const handleStop = useCallback(() => {
    const target = streamRef.current;
    if (!target) return;
    bridge.abortChat(target.requestId);
    setStreaming(null);
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streamRef.current) return;

      const now = Date.now();
      const userMessage: ChatMessage = {
        id: createId(),
        role: 'user',
        content: trimmed,
        createdAt: now,
      };
      const assistantMessage: ChatMessage = {
        id: createId(),
        role: 'assistant',
        content: '',
        createdAt: now + 1,
      };

      // Resolve the destination up front — a brand new chat needs an id before
      // we can point the stream at it.
      const conversationId = activeId ?? createId();
      const requestId = createId();

      setConversations((prev) => {
        const existing = prev.find((c) => c.id === conversationId);
        const base: Conversation = existing ?? {
          id: conversationId,
          title: 'New chat',
          messages: [],
          createdAt: now,
          updatedAt: now,
        };

        const updated: Conversation = {
          ...base,
          title: base.messages.length === 0 ? titleFrom(trimmed) : base.title,
          messages: [...base.messages, userMessage, assistantMessage],
          updatedAt: now,
        };

        // Send using the history we just computed, so nothing is missed.
        const wire = updated.messages
          .filter((m) => !m.error && (m.role === 'user' || m.content.length > 0))
          .map((m) => ({ role: m.role, content: m.content }));

        bridge.sendChat({ requestId, model, messages: wire });

        return existing
          ? prev.map((c) => (c.id === conversationId ? updated : c))
          : [updated, ...prev];
      });

      setActiveId(conversationId);
      setStreaming({ requestId, conversationId, messageId: assistantMessage.id });
    },
    [activeId, model],
  );

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={handleSelect}
        onNewChat={handleNewChat}
        onDelete={handleDelete}
      />

      <main className="main">
        <header className="topbar">
          <div className="topbar-title">{activeConversation?.title ?? 'New chat'}</div>
          <button
            className="ghost-button"
            onClick={handleClear}
            disabled={!activeConversation || activeConversation.messages.length === 0}
          >
            Clear chat
          </button>
        </header>

        <MessageList messages={activeConversation?.messages ?? []} isStreaming={isStreaming} />

        <Composer onSend={handleSend} onStop={handleStop} isStreaming={isStreaming} />

        <StatusBar
          status={status}
          model={model}
          onModelChange={setModel}
          onRetry={() => void refreshStatus()}
        />
      </main>
    </div>
  );
}
