import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Sidebar from './components/Sidebar';
import MessageList from './components/MessageList';
import Composer, { type ComposerHandle } from './components/Composer';
import SettingsDialog from './components/SettingsDialog';
import { bridge, newRequestId } from './lib/bridge';
import { useStreamBuffer } from './lib/useStreamBuffer';
import type {
  ChatMessage,
  Conversation,
  ConversationSummary,
  OllamaStatus,
  Settings,
  StreamState,
} from '../shared/types';

interface ActiveStream {
  requestId: string;
  messageId: string | null;
}

export default function App() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [active, setActive] = useState<Conversation | null>(null);
  const [status, setStatus] = useState<OllamaStatus | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [stream, setStream] = useState<ActiveStream | null>(null);
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const [trimmed, setTrimmed] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const buffer = useStreamBuffer();
  const composerRef = useRef<ComposerHandle>(null);

  // Listeners are registered once and read live values from refs, so they never
  // close over stale state.
  //
  // These refs are written synchronously at the point of action, NOT during
  // render. Main persists a turn to local SQLite and emits chat:started in
  // microseconds — well before React re-renders — so a ref updated on render
  // would still hold null when the event arrives and the turn would be dropped.
  const streamRef = useRef<ActiveStream | null>(null);
  const activeIdRef = useRef<string | null>(null);
  const conversationsRef = useRef<ConversationSummary[]>([]);

  const setStreamSafe = useCallback((next: ActiveStream | null) => {
    streamRef.current = next;
    setStream(next);
  }, []);

  const setActiveIdSafe = useCallback((next: string | null) => {
    activeIdRef.current = next;
    setActiveId(next);
  }, []);

  const setConversationsSafe = useCallback((next: ConversationSummary[]) => {
    conversationsRef.current = next;
    setConversations(next);
  }, []);

  const isStreaming = stream !== null;

  /* ------------------------------------------------------------ bootstrap */

  const refreshList = useCallback(async () => {
    setConversationsSafe(await bridge.conversations.list());
  }, [setConversationsSafe]);

  useEffect(() => {
    void (async () => {
      const [list, loadedSettings, loadedStatus] = await Promise.all([
        bridge.conversations.list(),
        bridge.settings.get(),
        bridge.ollama.status(),
      ]);
      setConversationsSafe(list);
      setSettings(loadedSettings);
      setStatus(loadedStatus);

      if (list.length > 0) {
        setActiveIdSafe(list[0].id);
        setActive(await bridge.conversations.get(list[0].id));
      }
    })();
  }, [setConversationsSafe, setActiveIdSafe]);

  // Theme: clearing the attribute lets prefers-color-scheme take over.
  useEffect(() => {
    if (!settings) return;
    if (settings.theme === 'system') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = settings.theme;
  }, [settings]);

  /* ------------------------------------------------------------- streaming */

  useEffect(() => {
    const offStarted = bridge.on('chat:started', (payload) => {
      if (streamRef.current?.requestId !== payload.requestId) return;

      setStreamSafe({ requestId: payload.requestId, messageId: payload.assistantMessage.id });
      setActive((prev) =>
        prev && prev.id === payload.conversationId
          ? {
              ...prev,
              title: payload.title,
              messages: [...prev.messages, payload.userMessage, payload.assistantMessage],
            }
          : prev,
      );
      void refreshList();
    });

    const offState = bridge.on('chat:state', (payload) => {
      if (streamRef.current?.requestId !== payload.requestId) return;
      setStreamState(payload.state);
    });

    const offDelta = bridge.on('chat:delta', (payload) => {
      if (streamRef.current?.requestId !== payload.requestId) return;
      buffer.append({ content: payload.content, thinking: payload.thinking });
    });

    const offTrimmed = bridge.on('chat:trimmed', (payload) => {
      if (streamRef.current?.requestId !== payload.requestId) return;
      setTrimmed(payload.droppedMessages);
    });

    const offEnd = bridge.on('chat:end', (payload) => {
      const current = streamRef.current;
      if (current?.requestId !== payload.requestId) return;

      // Read the ref, not state: it includes deltas not yet committed to a frame.
      const finalText = buffer.read();
      const messageId = current.messageId;

      setActive((prev) => {
        if (!prev || !messageId) return prev;
        return {
          ...prev,
          messages: prev.messages.map((m) =>
            m.id === messageId
              ? {
                  ...m,
                  content: finalText.content,
                  thinking: finalText.thinking,
                  status:
                    payload.outcome.kind === 'complete'
                      ? 'complete'
                      : payload.outcome.kind === 'cancelled'
                        ? 'cancelled'
                        : 'error',
                  error:
                    payload.outcome.kind === 'error'
                      ? payload.outcome.error
                      : payload.thinkingIncomplete
                        ? {
                            code: 'STREAM_MALFORMED' as const,
                            message: 'The model’s reasoning block was never closed.',
                          }
                        : undefined,
                  stats: payload.outcome.kind === 'complete' ? payload.outcome.stats : undefined,
                }
              : m,
          ),
        };
      });

      setStreamSafe(null);
      setStreamState(null);
      buffer.reset();
      void refreshList();
    });

    const offStatus = bridge.on('ollama:status-changed', setStatus);
    const offSettings = bridge.on('settings:changed', setSettings);

    return () => {
      offStarted();
      offState();
      offDelta();
      offTrimmed();
      offEnd();
      offStatus();
      offSettings();
    };
  }, [buffer, refreshList]);

  /* --------------------------------------------------------------- actions */

  const selectConversation = useCallback(
    async (id: string) => {
      if (streamRef.current) bridge.chat.abort(streamRef.current.requestId);
      setStreamSafe(null);
      setStreamState(null);
      buffer.reset();
      setTrimmed(null);
      setActiveIdSafe(id);
      setActive(await bridge.conversations.get(id));
    },
    [buffer],
  );

  const handleNewChat = useCallback(async () => {
    if (streamRef.current) bridge.chat.abort(streamRef.current.requestId);
    setStreamSafe(null);
    setStreamState(null);
    buffer.reset();
    setTrimmed(null);

    // Reuse an existing empty chat rather than stacking up blanks.
    const empty = conversationsRef.current.find((c) => c.messageCount === 0);
    if (empty) {
      setActiveIdSafe(empty.id);
      setActive(await bridge.conversations.get(empty.id));
      return;
    }

    const created = await bridge.conversations.create();
    setActiveIdSafe(created.id);
    setActive(created);
    await refreshList();
  }, [buffer, refreshList]);

  const handleDelete = useCallback(
    async (id: string) => {
      // Confirm before destroying anything the user might miss. An empty chat
      // skips the prompt; main decides that so the rule lives in one place.
      const target = conversationsRef.current.find((c) => c.id === id);
      const confirmed = await bridge.conversations.confirmDelete(
        target?.title ?? 'this chat',
        target?.messageCount ?? 0,
      );
      if (!confirmed) return;

      if (streamRef.current) bridge.chat.abort(streamRef.current.requestId);
      setStreamSafe(null);
      await bridge.conversations.remove(id);
      const list = await bridge.conversations.list();
      setConversationsSafe(list);

      if (activeIdRef.current === id) {
        const next = list[0];
        setActiveIdSafe(next?.id ?? null);
        setActive(next ? await bridge.conversations.get(next.id) : null);
      }
    },
    [],
  );

  const handleRename = useCallback(
    async (id: string, title: string) => {
      await bridge.conversations.rename(id, title);
      setActive((prev) => (prev && prev.id === id ? { ...prev, title } : prev));
      await refreshList();
    },
    [refreshList],
  );

  const handleClear = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    if (streamRef.current) bridge.chat.abort(streamRef.current.requestId);
    setStreamSafe(null);
    buffer.reset();
    await bridge.conversations.clear(id);
    setActive(await bridge.conversations.get(id));
    await refreshList();
  }, [buffer, refreshList]);

  const handleStop = useCallback(() => {
    const current = streamRef.current;
    if (current) bridge.chat.abort(current.requestId);
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      if (streamRef.current) return;

      let conversationId = activeIdRef.current;
      if (!conversationId) {
        const created = await bridge.conversations.create();
        conversationId = created.id;
        setActiveIdSafe(conversationId);
        setActive(created);
        await refreshList();
      }

      buffer.reset();
      setTrimmed(null);
      const requestId = newRequestId();
      setStreamSafe({ requestId, messageId: null });
      setStreamState('queued');
      bridge.chat.start({ conversationId, requestId, userText: text });
    },
    [buffer, refreshList],
  );

  /* -------------------------------------------------------------- keyboard */

  // Escape stops generation, but only when nothing else owns the key. It is
  // handled here rather than as a menu accelerator because an accelerator is
  // captured before the renderer sees it, which would break Escape for the
  // settings dialog and the inline rename field.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (settingsOpen) return; // the dialog closes itself
      const tag = (document.activeElement?.tagName ?? '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return; // rename / composer
      if (!streamRef.current) return;
      bridge.chat.abort(streamRef.current.requestId);
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [settingsOpen]);

  /* ----------------------------------------------------------- menu wiring */

  useEffect(() => {
    const step = (direction: 1 | -1) => {
      const list = conversationsRef.current;
      if (list.length === 0) return;
      const index = list.findIndex((c) => c.id === activeIdRef.current);
      const next = list[Math.min(list.length - 1, Math.max(0, index + direction))];
      if (next && next.id !== activeIdRef.current) void selectConversation(next.id);
    };

    const offs = [
      bridge.on('menu:new-chat', () => void handleNewChat()),
      bridge.on('menu:delete-chat', () => {
        if (activeIdRef.current) void handleDelete(activeIdRef.current);
      }),
      bridge.on('menu:open-settings', () => setSettingsOpen(true)),
      bridge.on('menu:focus-composer', () => composerRef.current?.focus()),
      bridge.on('menu:stop-generating', handleStop),
      bridge.on('menu:prev-chat', () => step(-1)),
      bridge.on('menu:next-chat', () => step(1)),
    ];
    return () => offs.forEach((off) => off());
  }, [handleNewChat, handleDelete, handleStop, selectConversation]);

  /* ---------------------------------------------------------------- render */

  // Overlay the live buffer onto the streaming message only; every other
  // message keeps its object identity so MessageItem's memo holds.
  const displayMessages: ChatMessage[] = useMemo(() => {
    if (!active) return [];
    if (!stream?.messageId) return active.messages;
    return active.messages.map((m) =>
      m.id === stream.messageId
        ? { ...m, content: buffer.live.content, thinking: buffer.live.thinking }
        : m,
    );
  }, [active, stream, buffer.live]);

  const connected = status?.connected === true && status.activeModelInstalled;
  const draft = activeId ? (drafts[activeId] ?? '') : (drafts.__new ?? '');

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        status={status}
        onSelect={(id) => void selectConversation(id)}
        onNewChat={() => void handleNewChat()}
        onDelete={(id) => void handleDelete(id)}
        onRename={(id, title) => void handleRename(id, title)}
        onOpenSettings={() => setSettingsOpen(true)}
        onRetryConnection={() => void bridge.ollama.refresh().then(setStatus)}
      />

      <main className="main">
        <header className="topbar">
          <div className="topbar-title" data-testid="chat-title">
            {active?.title ?? 'New chat'}
          </div>
          <button
            type="button"
            className="ghost-button"
            onClick={() => void handleClear()}
            disabled={!active || active.messages.length === 0}
            data-testid="clear-chat"
          >
            Clear chat
          </button>
        </header>

        <MessageList
          messages={displayMessages}
          streamingMessageId={stream?.messageId ?? null}
          streamState={streamState}
          showThinkingByDefault={settings?.showThinkingByDefault ?? false}
          trimmedNotice={trimmed}
        />

        <Composer
          ref={composerRef}
          onSend={(text) => void handleSend(text)}
          onStop={handleStop}
          isStreaming={isStreaming}
          disabled={!connected}
          sendOnEnter={settings?.sendOnEnter ?? true}
          draft={draft}
          onDraftChange={(text) =>
            setDrafts((prev) => ({ ...prev, [activeId ?? '__new']: text }))
          }
        />
      </main>

      {settingsOpen && settings ? (
        <SettingsDialog
          settings={settings}
          status={status}
          onUpdate={(patch) => void bridge.settings.update(patch).then(setSettings)}
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}
    </div>
  );
}
