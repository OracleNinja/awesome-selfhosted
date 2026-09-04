import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
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
// SNIPPET_MATCH_OPEN/CLOSE are runtime values (private-use code points), not
// types, so they need a normal import alongside the `import type` below.
import { SNIPPET_MATCH_OPEN, SNIPPET_MATCH_CLOSE } from '../shared/types';
import type {
  ChatMessage,
  Conversation,
  ConversationSummary,
  AnsweredSource,
  ExportFormat,
  OllamaStatus,
  PublicSettings,
  SearchResult,
  StreamState,
} from '../shared/types';

interface ActiveStream {
  requestId: string;
  messageId: string | null;
}

// Debounced rather than fired per keystroke: FTS5 queries are cheap, but
// there is no reason to hit the store on every character while someone is
// still typing a word.
const SEARCH_DEBOUNCE_MS = 300;

const searchTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
});

function formatSearchTimestamp(createdAt: number): string {
  return searchTimeFormatter.format(new Date(createdAt));
}

/**
 * `SearchHit.snippet` wraps matched runs in SNIPPET_MATCH_OPEN/CLOSE, which
 * are private-use code points rather than markup (see shared/types.ts). This
 * splits on them and renders plain text nodes with matched runs wrapped in
 * <mark> — never dangerouslySetInnerHTML — so a conversation's own text can
 * never inject markup here, no matter what it contains.
 */
function renderSnippet(snippet: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const segments = snippet.split(SNIPPET_MATCH_OPEN);

  segments.forEach((segment, index) => {
    if (index === 0) {
      if (segment) nodes.push(segment);
      return;
    }
    const [match, ...rest] = segment.split(SNIPPET_MATCH_CLOSE);
    nodes.push(<mark key={`match-${index}`}>{match}</mark>);
    const tail = rest.join(SNIPPET_MATCH_CLOSE);
    if (tail) nodes.push(tail);
  });

  return nodes;
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
  // Search panel, opened via `menu:search`. `searchResult` is null until the
  // first debounced query resolves; distinguishing that from "zero hits" is
  // what lets the UI tell "nothing typed yet" apart from "typed, no matches".
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [stream, setStream] = useState<ActiveStream | null>(null);
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const [trimmed, setTrimmed] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [voice, setVoice] = useState<VoiceCapabilities | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [sources, setSources] = useState<AnsweredSource[]>([]);

  const buffer = useStreamBuffer();
  const composerRef = useRef<ComposerHandle>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  // Invalidates a stale in-flight search response — see the debounce effect
  // below. Bumped on every query change (including going back to empty) and
  // on close, so a slow response for an old query can never overwrite what
  // the user is looking at now.
  const searchSeqRef = useRef(0);

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

  /* --------------------------------------------------------------- search */

  // Menu-triggered. Always starts from a clean slate so a previous session's
  // query and results never flash before the debounce effect below runs.
  const handleOpenSearch = useCallback(() => {
    setSearchQuery('');
    setSearchResult(null);
    setSearchOpen(true);
  }, []);

  const closeSearch = useCallback(() => {
    setSearchOpen(false);
    setSearchQuery('');
    setSearchResult(null);
  }, []);

  const handleSelectSearchResult = useCallback(
    (conversationId: string) => {
      // Wait for the conversation to actually load before closing, so the
      // panel never closes onto a stale title/transcript.
      void selectConversation(conversationId).then(closeSearch);
    },
    [selectConversation, closeSearch],
  );

  // Debounced full-text search. The empty/whitespace case is handled locally
  // and never reaches the store: SearchResult.hits is empty for an
  // unindexable query too, so treating it identically to "zero matches"
  // would show "No results" for a box the user has not finished typing in.
  useEffect(() => {
    // Every run invalidates whatever request is still in flight for the
    // previous query (or for being open at all), not just the pending
    // timer: a debounce only cancels a timer that has not fired yet, but the
    // IPC round trip it already started can still resolve out of order.
    const seq = ++searchSeqRef.current;
    if (!searchOpen) return;

    const trimmed = searchQuery.trim();
    if (trimmed === '') {
      setSearchResult(null);
      return;
    }

    const timer = setTimeout(() => {
      void bridge.conversations.search({ query: trimmed }).then((result) => {
        if (searchSeqRef.current !== seq) return; // superseded by a newer query
        setSearchResult(result);
      });
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [searchQuery, searchOpen]);

  // Focus moves to the input every time the panel opens, matching how the
  // export chooser and settings dialog behave.
  useEffect(() => {
    if (searchOpen) searchInputRef.current?.focus();
  }, [searchOpen]);

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
      if (settingsOpen || exportPrompt || searchOpen) return; // these dialogs close themselves
      const tag = (document.activeElement?.tagName ?? '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return; // rename / composer
      if (!streamRef.current) return;
      bridge.chat.abort(streamRef.current.requestId);
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [settingsOpen, exportPrompt, searchOpen]);

  useEffect(() => {
    if (!exportPrompt) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeExportPrompt();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [exportPrompt, closeExportPrompt]);

  useEffect(() => {
    if (!searchOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeSearch();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [searchOpen, closeSearch]);

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
      bridge.on('menu:search', handleOpenSearch),
    ];
    return () => offs.forEach((off) => off());
  }, [handleNewChat, handleDelete, handleStop, selectConversation, handleExportChat, handleOpenSearch]);

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
  // A trimmed empty query is "nothing typed yet", not "typed, zero matches" —
  // it must never render the empty-result message.
  const hasSearchQuery = searchQuery.trim() !== '';
  const searchHasError = searchResult?.error !== undefined;

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

      {searchOpen ? (
        <div className="modal-backdrop" onMouseDown={closeSearch} data-testid="search-dialog">
          <div
            className="modal search-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Find in conversations"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <header className="modal-header">
              <h2>Find in Conversations</h2>
              <button
                type="button"
                className="ghost-button"
                onClick={closeSearch}
                aria-label="Close"
              >
                ×
              </button>
            </header>

            <div className="modal-body">
              <label className="field">
                <span className="field-label">Search</span>
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search titles and messages…"
                  autoComplete="off"
                  data-testid="search-input"
                />
              </label>

              {searchHasError ? (
                <p className="field-hint" role="alert" data-testid="search-error">
                  {searchResult?.error?.message}
                </p>
              ) : null}

              {!searchHasError && searchResult?.truncated ? (
                <p className="field-hint" data-testid="search-truncated">
                  Showing the first {searchResult.hits.length} matches — refine your search to
                  narrow it down.
                </p>
              ) : null}

              {hasSearchQuery && searchResult && !searchHasError ? (
                searchResult.hits.length === 0 ? (
                  <p className="field-hint" data-testid="search-empty">
                    No results found.
                  </p>
                ) : (
                  <ul className="search-results" data-testid="search-results">
                    {searchResult.hits.map((hit) => (
                      // messageId is present on a content hit and absent on a
                      // title hit (see shared/types.ts SearchHit), so it alone
                      // cannot key every row -- every title hit for this query
                      // would collapse to the same undefined key. conversationId
                      // is unique per title hit (conversations_fts mirrors
                      // conversations 1:1, so a query matches a conversation's
                      // title at most once), so it stands in for the missing
                      // messageId. The matchedIn prefix keeps a content hit's
                      // messageId and a title hit's conversationId from ever
                      // colliding even in principle, since they are drawn from
                      // different id spaces.
                      <li key={`${hit.matchedIn}-${hit.messageId ?? hit.conversationId}`}>
                        <button
                          type="button"
                          className="search-result"
                          onClick={() => handleSelectSearchResult(hit.conversationId)}
                          data-testid="search-result"
                        >
                          {/*
                            A content hit's snippet excerpts the matching
                            message, so the plain title above it tells the user
                            which conversation that message lives in. A title
                            hit's snippet *is* the title (highlighted) -- see
                            shared/types.ts -- so repeating the plain title
                            above it would show the same text twice with only
                            highlighting as the difference. Showing the
                            highlighted title once, labelled, carries the same
                            information without the duplication.
                          */}
                          {hit.matchedIn === 'content' ? (
                            <span className="search-result-title">{hit.title}</span>
                          ) : null}
                          <span className="search-result-snippet">{renderSnippet(hit.snippet)}</span>
                          {hit.matchedIn === 'title' ? (
                            <span className="search-result-kind">Title match</span>
                          ) : null}
                          <span className="search-result-time">
                            {formatSearchTimestamp(hit.createdAt)}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
