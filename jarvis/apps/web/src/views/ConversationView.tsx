/**
 * The conversation transcript.
 *
 * Sending goes through the runtime client, the same path the Control Room's
 * command bar uses — there is one way into the orchestrator from this app.
 * Tool calls and results are shown collapsed rather than hidden: if JARVIS did
 * something, the user can see exactly what.
 */
import { useEffect, useRef, useState } from 'react';
import { useRuntime, useRuntimeClient } from '../runtime/react';
import { api, type Message } from '../runtime/api';

export function ConversationView({
  conversationId,
  messages,
  onMessages,
  onConversation,
}: {
  conversationId: string | null;
  messages: Message[];
  onMessages: (messages: Message[]) => void;
  onConversation: (id: string) => void;
}) {
  const client = useRuntimeClient();
  const [draft, setDraft] = useState('');
  const busy = useRuntime((state) => state.commandInFlight);
  const activeModel = useRuntime((state) => state.snapshot?.activeModel ?? null);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, busy]);

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    const result = await client.sendCommand(text, conversationId);
    if (result.conversationId) {
      onConversation(result.conversationId);
      const reloaded = await api.messages(result.conversationId).catch(() => null);
      if (reloaded) onMessages(reloaded.messages);
    }
  };

  const visible = messages.filter((message) => message.role !== 'system');

  return (
    <>
      <div className="messages">
        {visible.length === 0 && (
          <div className="empty">
            JARVIS is online.
            <br />
            <span className="small">
              {activeModel
                ? `${activeModel.provider} · ${activeModel.model}${activeModel.available ? '' : ' · NOT CONFIGURED'}`
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
                  <summary>{message.toolCalls.map((call) => call.name).join(', ')}</summary>
                  <pre style={{ margin: '6px 0 0' }}>{JSON.stringify(message.toolCalls, null, 2)}</pre>
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

      <div className="composer">
        <div className="composer-row">
          <textarea
            className="textarea grow"
            rows={2}
            placeholder="Message JARVIS…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
          />
          <button className="btn btn-primary" onClick={() => void send()} disabled={busy || !draft.trim()}>
            Send
          </button>
        </div>
        <div className="small muted">
          Voice controls live in the Control Room command bar.
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
