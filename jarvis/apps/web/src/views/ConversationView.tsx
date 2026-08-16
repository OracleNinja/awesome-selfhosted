import { useEffect, useRef, useState } from 'react';
import type { Message, SystemStatus } from '../api';
import {
  speechRecognitionSupported,
  speechSynthesisSupported,
  speakInBrowser,
  startDictation,
  stopSpeaking,
  type DictationHandle,
} from '../voice';

/**
 * The conversation.
 *
 * Tool calls and tool results are shown, collapsed, rather than hidden: if
 * JARVIS did something, the user can see exactly what it did.
 */
export function ConversationView({
  messages,
  busy,
  status,
  onSend,
  lastReply,
}: {
  messages: Message[];
  busy: boolean;
  status: SystemStatus | null;
  onSend: (text: string) => Promise<void>;
  lastReply: string;
}) {
  const [draft, setDraft] = useState('');
  const [listening, setListening] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(false);
  const [voiceError, setVoiceError] = useState('');
  const dictation = useRef<DictationHandle | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const spokenRef = useRef('');

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, busy]);

  // Speak new replies when voice output is on.
  useEffect(() => {
    if (!speakReplies || !lastReply || lastReply === spokenRef.current) return;
    spokenRef.current = lastReply;
    if (!speakInBrowser(lastReply)) {
      setVoiceError('This browser does not support speech synthesis.');
    }
  }, [lastReply, speakReplies]);

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    await onSend(text);
  };

  const toggleMic = () => {
    setVoiceError('');
    if (listening) {
      dictation.current?.stop();
      return;
    }
    const handle = startDictation({
      onPartial: (text) => setDraft(text),
      onFinal: (text) => setDraft(text),
      onError: (message) => setVoiceError(message),
      onEnd: () => setListening(false),
    });
    if (!handle) {
      setVoiceError('Speech recognition is not available in this browser.');
      return;
    }
    dictation.current = handle;
    setListening(true);
  };

  const sttMode = status?.config.sttProvider ?? 'browser';
  const ttsMode = status?.config.ttsProvider ?? 'browser';
  const micUsable = sttMode === 'browser' ? speechRecognitionSupported() : true;
  const speakerUsable = ttsMode === 'browser' ? speechSynthesisSupported() : true;

  const visible = messages.filter((message) => message.role !== 'system');

  return (
    <>
      <div className="messages">
        {visible.length === 0 && (
          <div className="empty">
            JARVIS is online.
            <br />
            <span className="small">
              {status
                ? `${status.activeModelProvider} · ${status.activeModel} · ${status.tools.total} tools · ${status.agents.length} agents`
                : 'Connecting…'}
            </span>
          </div>
        )}

        {visible.map((message) => {
          if (message.role === 'assistant' && message.toolCalls?.length) {
            return (
              <div className="msg toolcall" key={message.id}>
                <span className="msg-role">tool call</span>
                <details className="msg-body">
                  <summary>
                    {message.toolCalls.map((call) => call.name).join(', ')}
                  </summary>
                  <pre style={{ margin: '6px 0 0' }}>
                    {JSON.stringify(message.toolCalls, null, 2)}
                  </pre>
                </details>
              </div>
            );
          }
          if (message.role === 'tool') {
            return (
              <div className="msg tool" key={message.id}>
                <span className="msg-role">{message.name ?? 'tool'} result</span>
                <details className="msg-body">
                  <summary>{summarise(message.content)}</summary>
                  <pre style={{ margin: '6px 0 0' }}>{message.content}</pre>
                </details>
              </div>
            );
          }
          return (
            <div className={`msg ${message.role}`} key={message.id}>
              <span className="msg-role">{message.role === 'user' ? 'you' : 'jarvis'}</span>
              <div className="msg-body">{message.content}</div>
            </div>
          );
        })}

        {busy && (
          <div className="thinking">
            <span className="pulse" />
            JARVIS is working…
          </div>
        )}
        <div ref={bottom} />
      </div>

      {voiceError && <div className="error-banner">{voiceError}</div>}

      <div className="composer">
        <div className="composer-row">
          <textarea
            className="textarea grow"
            rows={2}
            placeholder={listening ? 'Listening…' : 'Message JARVIS…'}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
          />
          <button
            className={`btn ${listening ? 'mic-on' : ''}`}
            onClick={toggleMic}
            disabled={!micUsable}
            title={
              micUsable
                ? listening
                  ? 'Stop listening'
                  : 'Speak your message'
                : 'Speech recognition is not available here'
            }
            aria-pressed={listening}
          >
            {listening ? '■' : '🎙'}
          </button>
          <button className="btn btn-primary" onClick={() => void send()} disabled={busy || !draft.trim()}>
            Send
          </button>
        </div>
        <div className="row small muted" style={{ gap: 12 }}>
          <label className="row" style={{ gap: 6, cursor: speakerUsable ? 'pointer' : 'not-allowed' }}>
            <input
              type="checkbox"
              checked={speakReplies}
              disabled={!speakerUsable}
              onChange={(event) => {
                setSpeakReplies(event.target.checked);
                if (!event.target.checked) stopSpeaking();
              }}
            />
            Speak replies
          </label>
          <span>
            voice in: {sttMode}
            {!micUsable && ' (unavailable in this browser)'} · voice out: {ttsMode}
            {!speakerUsable && ' (unavailable in this browser)'}
          </span>
        </div>
      </div>
    </>
  );
}

function summarise(content: string): string {
  try {
    const parsed = JSON.parse(content) as { summary?: string; ok?: boolean };
    if (parsed.summary) return `${parsed.ok === false ? '✕ ' : '✓ '}${parsed.summary}`;
  } catch {
    /* not JSON */
  }
  return content.length > 110 ? `${content.slice(0, 110)}…` : content;
}
