import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import MessageItem from './MessageItem';
import type { ChatMessage, StreamState } from '../../shared/types';

interface Props {
  messages: ChatMessage[];
  streamingMessageId: string | null;
  streamState: StreamState | null;
  showThinkingByDefault: boolean;
  trimmedNotice: number | null;
}

/** Within this distance of the bottom, the view keeps following new text. */
const STICK_THRESHOLD_PX = 80;

export default function MessageList({
  messages,
  streamingMessageId,
  streamState,
  showThinkingByDefault,
  trimmedNotice,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [showJump, setShowJump] = useState(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      stick.current = distance < STICK_THRESHOLD_PX;
      setShowJump(!stick.current && el.scrollHeight > el.clientHeight * 1.2);
    };

    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Follow new text only when the user has not scrolled away.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !stick.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  const jumpToLatest = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stick.current = true;
    setShowJump(false);
  }, []);

  if (messages.length === 0) {
    return (
      <div className="messages" ref={scrollRef} data-testid="message-list">
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
    <div className="messages-wrap">
      <div className="messages" ref={scrollRef} data-testid="message-list">
        <div className="messages-inner">
          {trimmedNotice ? (
            <div className="trim-divider" data-testid="trim-divider">
              Earlier messages trimmed to fit the context window ({trimmedNotice} dropped)
            </div>
          ) : null}

          {messages.map((message) => (
            <MessageItem
              key={message.id}
              message={message}
              isStreaming={message.id === streamingMessageId}
              isLoadingModel={message.id === streamingMessageId && streamState === 'loading-model'}
              showThinkingByDefault={showThinkingByDefault}
            />
          ))}
        </div>
      </div>

      {showJump ? (
        <button type="button" className="jump-button" onClick={jumpToLatest}>
          Jump to latest ↓
        </button>
      ) : null}
    </div>
  );
}
