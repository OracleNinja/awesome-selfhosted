import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Sidebar from './components/Sidebar';
import MessageList from './components/MessageList';
import Composer, { type ComposerHandle } from './components/Composer';
import SettingsDialog from './components/SettingsDialog';
import ModeChooser from './components/ModeChooser';
import SourceList from './components/SourceList';
import { bridge, newRequestId } from './lib/bridge';
import { useStreamBuffer } from './lib/useStreamBuffer';
import { useVoiceInput } from './lib/useVoiceInput';
import { speakableText } from '../shared/speakable';
import type { VoiceCapabilities } from '../shared/voice';
import type {
  ChatMessage,
  Conversation,
  ConversationSummary,
  AnsweredSource,
  ExportFormat,
  OllamaStatus,
  PublicSettings,
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
  const [settings, setSettings] = useState<PublicSettings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Export is a small in-app chooser (format + reasoning) rather than a
  // native submenu-per-format: `menu:export-chat` carries no payload (see
  // shared/ipc.ts), so the native menu has exactly one trigger point and the
  // actual choice has to be made in the renderer, where the active
  // conversation id already lives. Reuses the existing modal/field CSS
  // classes (see ModeChooser/SettingsDialog) rather than introducing new
  // styling.
  const [exportPrompt, setExportPrompt] = useState(false);
  const [exportIncludeReasoning, setExportIncludeReasoning] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [stream, setStream] = useState<ActiveStream | null>(null);
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const [trimmed, setTrimmed] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [voice, setVoice] = useState<VoiceCapabilities | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [sources, setSources] = useState<AnsweredSource[]>([]);

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
  // Whether the in-flight turn was dictated. A spoken prompt always gets a
  // spoken reply, regardless of the global setting.
  const turnFromVoiceRef = useRef(false);
  const settingsRef = useRef<PublicSettings | null>(null);
  settingsRef.current = settings;

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

  useEffect(() => {
    void bridge.voice.capabilities().then(setVoice);
  }, []);

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

      // Speak the answer when the prompt was dictated, or when the user has
      // asked for every reply to be spoken.
      const shouldSpeak =
        payload.outcome.kind !== 'error' &&
        (turnFromVoiceRef.current || settingsRef.current?.speakResponses === true);

      if (shouldSpeak) {
        const spoken = speakableText(finalText.content);
        if (spoken) {
          void bridge.voice.speak({
            requestId: payload.requestId,
            text: spoken,
            voice: settingsRef.current?.voiceName ?? '',
            rate: settingsRef.current?.speechRate ?? 175,
          });
        }
      }
      turnFromVoiceRef.current = false;

      setStreamSafe(null);
      setStreamState(null);
      buffer.reset();
      void refreshList();
    });

    const offStatus = bridge.on('ollama:status-changed', setStatus);
    const offSettings = bridge.on('settings:changed', setSettings);
    const offTts = bridge.on('tts:state', (payload) => setIsSpeaking(payload.speaking));
    const offSources = bridge.on('chat:sources', (payload) => {
      if (streamRef.current?.requestId !== payload.requestId) return;
      setSources(payload.sources);
    });

    return () => {
      offStarted();
      offState();
      offDelta();
      offTrimmed();
      offEnd();
      offStatus();
      offSettings();
      offTts();
      offSources();
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

  /* --------------------------------------------------------------- export */

  const closeExportPrompt = useCallback(() => {
    setExportPrompt(false);
    setExportError(null);
    setExportBusy(false);
  }, []);

  // Menu-triggered. Opening with no active conversation would send a
  // malformed request (no id to export), so it is a silent no-op instead.
  const handleExportChat = useCallback(() => {
    if (!activeIdRef.current) return;
    setExportIncludeReasoning(false);
    setExportError(null);
    setExportPrompt(true);
  }, []);

  const runExport = useCallback(
    async (format: ExportFormat) => {
      const id = activeIdRef.current;
      if (!id) {
        closeExportPrompt();
        return;
      }

      setExportBusy(true);
      setExportError(null);
      const result = await bridge.conversations.export({
        id,
        format,
        includeReasoning: exportIncludeReasoning,
      });
      setExportBusy(false);

      if (result.status === 'error') {
        // Shown as-is: main already writes a message that is safe to display.
        setExportError(result.message);
        return;
      }

      // 'saved': confirmed quietly by simply closing — no extra toast.
      // 'cancelled': a deliberate choice, not a failure, so it ends the same
      // way with nothing alarming shown.
      closeExportPrompt();
    },
    [exportIncludeReasoning, closeExportPrompt],
  );

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
      setSources([]);
      const requestId = newRequestId();
      setStreamSafe({ requestId, messageId: null });
      setStreamState('queued');
      bridge.chat.start({ conversationId, requestId, userText: text });
    },
    [buffer, refreshList],
  );

  /* ----------------------------------------------------------------- voice */

  // Dictated text goes through the ordinary send path; nothing about the chat
  // pipeline, the Ollama client or persistence is aware that voice exists.
  const handleTranscript = useCallback(
    (text: string) => {
      turnFromVoiceRef.current = true;
      void handleSend(text);
    },
    [handleSend],
  );

  const voiceInput = useVoiceInput(settings?.sttLocale ?? 'en-US', handleTranscript);

  const handleMicToggle = useCallback(() => {
    if (voiceInput.state === 'recording') void voiceInput.stop();
    else void voiceInput.start();
  }, [voiceInput]);

  const handleStopSpeaking = useCallback(() => {
    void bridge.voice.stopSpeaking();
  }, []);

  /* -------------------------------------------------------------- keyboard */

  // Escape stops generation, but only when nothing else owns the key. It is
  // handled here rather than as a menu accelerator because an accelerator is
  // captured before the renderer sees it, which would break Escape for the
  // settings dialog and the inline rename field.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (settingsOpen || exportPrompt) return; // these dialogs close themselves
      const tag = (document.activeElement?.tagName ?? '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return; // rename / composer
      if (!streamRef.current) return;
      bridge.chat.abort(streamRef.current.requestId);
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [settingsOpen, exportPrompt]);

  useEffect(() => {
    if (!exportPrompt) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeExportPrompt();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [exportPrompt, closeExportPrompt]);

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
      bridge.on('menu:export-chat', handleExportChat),
    ];
    return () => offs.forEach((off) => off());
  }, [handleNewChat, handleDelete, handleStop, selectConversation, handleExportChat]);

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
        mode={settings?.mode ?? 'local'}
        onlineConfigured={Boolean(settings?.gatewayTokenConfigured && settings?.gatewayUrl)}
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

        {sources.length > 0 ? <SourceList sources={sources} /> : null}

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
          voiceAvailable={voice?.stt.available ?? false}
          voiceUnavailableReason={voice?.stt.reason}
          recordingState={voiceInput.state}
          onMicToggle={handleMicToggle}
          voiceError={voiceInput.error?.message ?? null}
          onDismissVoiceError={voiceInput.clearError}
          isSpeaking={isSpeaking}
          onStopSpeaking={handleStopSpeaking}
        />
      </main>

      {settings && !settings.modeChosen ? (
        <ModeChooser
          onChoose={(mode) =>
            void bridge.settings.update({ mode, modeChosen: true }).then(setSettings)
          }
        />
      ) : null}

      {settingsOpen && settings ? (
        <SettingsDialog
          settings={settings}
          status={status}
          voice={voice}
          onUpdate={(patch) => void bridge.settings.update(patch).then(setSettings)}
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}

      {exportPrompt ? (
        <div
          className="modal-backdrop"
          onMouseDown={closeExportPrompt}
          data-testid="export-chat-dialog"
        >
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="Export chat"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <header className="modal-header">
              <h2>Export chat</h2>
              <button
                type="button"
                className="ghost-button"
                onClick={closeExportPrompt}
                aria-label="Close"
              >
                ×
              </button>
            </header>

            <div className="modal-body">
              <label className="field field-inline">
                <input
                  type="checkbox"
                  checked={exportIncludeReasoning}
                  onChange={(e) => setExportIncludeReasoning(e.target.checked)}
                  data-testid="export-include-reasoning"
                />
                <span>Include reasoning</span>
              </label>

              {exportError ? (
                <p className="field-hint" role="alert" data-testid="export-error">
                  {exportError}
                </p>
              ) : null}
            </div>

            <footer className="modal-footer">
              <button
                type="button"
                className="ghost-button"
                onClick={() => void runExport('markdown')}
                disabled={exportBusy}
                data-testid="export-as-markdown"
              >
                Export as Markdown
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => void runExport('json')}
                disabled={exportBusy}
                data-testid="export-as-json"
              >
                Export as JSON
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </div>
  );
}
