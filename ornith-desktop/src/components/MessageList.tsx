import { useEffect, useLayoutEffect, useRef } from 'react';
import Markdown from './Markdown';
import type { ChatMessage } from '../types';

interface Props {
  messages: ChatMessage[];
  isStreaming: boolean;
}

/** How close to the bottom still counts as "following along". */
const STICK_THRESHOLD_PX = 80;

export default function MessageList({ messages, isStreaming }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  // Track whether the user has scrolled away; if they have, don't yank them back.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      stickToBottom.current = distance < STICK_THRESHOLD_PX;
    };

    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickToBottom.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="messages" ref={scrollRef}>
        <div className="empty-state">
          <div className="empty-mark" aria-hidden="true">
            🐦
          </div>
          <h1>Ornith Desktop</h1>
          <p>Everything runs locally through Ollama. Nothing leaves this Mac.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="messages" ref={scrollRef}>
      <div className="messages-inner">
        {messages.map((message, index) => {
          const isLast = index === messages.length - 1;
          const pending = isStreaming && isLast && message.role === 'assistant';

          return (
            <article key={message.id} className={`message message-${message.role}`}>
              <div className="message-role">{message.role === 'user' ? 'You' : 'Ornith'}</div>

              <div className="message-body">
                {message.error ? (
                  <div className="message-error">
                    <strong>Couldn’t get a response.</strong>
                    <span>{message.error}</span>
                  </div>
                ) : message.content ? (
                  <Markdown content={message.content} />
                ) : pending ? (
                  <div className="typing" aria-label="Generating">
                    <span />
                    <span />
                    <span />
                  </div>
                ) : null}

                {pending && message.content ? <span className="caret" /> : null}
              </div>

              {message.tokensPerSecond ? (
                <div className="message-meta">{message.tokensPerSecond.toFixed(1)} tok/s</div>
              ) : null}
            </article>
          );
        })}
      </div>
    </div>
  );
}
