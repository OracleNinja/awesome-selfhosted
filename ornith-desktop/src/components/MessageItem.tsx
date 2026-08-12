import { memo } from 'react';
import Markdown from './Markdown';
import ThinkingPanel from './ThinkingPanel';
import type { ChatMessage } from '../../shared/types';

interface Props {
  message: ChatMessage;
  isStreaming: boolean;
  showThinkingByDefault: boolean;
  /** True when the model is loaded but no token has arrived yet. */
  isLoadingModel: boolean;
}

function MessageItem({ message, isStreaming, showThinkingByDefault, isLoadingModel }: Props) {
  const isUser = message.role === 'user';
  const hasContent = message.content.length > 0;
  const interrupted = message.status === 'cancelled';

  return (
    <article className={`message message-${message.role}`} data-testid={`message-${message.role}`}>
      <div className="message-role">{isUser ? 'You' : 'Ornith'}</div>

      <div className="message-body">
        {!isUser && message.thinking ? (
          <ThinkingPanel
            thinking={message.thinking}
            isStreaming={isStreaming}
            answerStarted={hasContent}
            defaultOpen={showThinkingByDefault}
            incomplete={message.error?.code === 'STREAM_MALFORMED'}
          />
        ) : null}

        {isUser ? (
          <div className="user-text">{message.content}</div>
        ) : hasContent ? (
          <Markdown content={message.content} />
        ) : isStreaming ? (
          <div className="typing" aria-label={isLoadingModel ? 'Loading model' : 'Generating'}>
            <span />
            <span />
            <span />
          </div>
        ) : null}

        {isStreaming && hasContent ? <span className="caret" aria-hidden="true" /> : null}

        {isLoadingModel && isStreaming ? (
          <div className="loading-note" data-testid="loading-model">
            Loading the model into memory…
          </div>
        ) : null}

        {message.error && message.status === 'error' ? (
          <div className="message-error" data-testid="message-error">
            <strong>Couldn’t get a response.</strong>
            <span>{message.error.message}</span>
          </div>
        ) : null}

        {interrupted ? (
          <div className="message-note" data-testid="message-stopped">
            {message.error?.code === 'STREAM_INTERRUPTED'
              ? 'This response was interrupted.'
              : 'Stopped.'}
          </div>
        ) : null}
      </div>

      {message.stats && message.stats.tokensPerSecond > 0 ? (
        <div className="message-meta">{message.stats.tokensPerSecond.toFixed(1)} tok/s</div>
      ) : null}
    </article>
  );
}

/**
 * Memoised so a streaming message does not force every completed message to
 * re-parse its Markdown on each frame.
 */
export default memo(MessageItem);
