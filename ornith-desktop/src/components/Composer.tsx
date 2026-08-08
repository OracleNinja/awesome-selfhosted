import { forwardRef, useCallback, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';

interface Props {
  onSend: (text: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
  sendOnEnter: boolean;
  /** Preserved per conversation so switching chats does not lose a draft. */
  draft: string;
  onDraftChange: (text: string) => void;
}

export interface ComposerHandle {
  focus(): void;
}

const MAX_HEIGHT_PX = 200;

const Composer = forwardRef<ComposerHandle, Props>(function Composer(
  { onSend, onStop, isStreaming, disabled, sendOnEnter, draft, onDraftChange },
  ref,
) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [rows, setRows] = useState(1);

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
  }));

  // Grow with content up to a cap, then scroll internally.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const next = Math.min(el.scrollHeight, MAX_HEIGHT_PX);
    el.style.height = `${next}px`;
    setRows(next >= MAX_HEIGHT_PX ? 8 : 1);
  }, [draft]);

  const submit = useCallback(() => {
    const trimmed = draft.trim();
    if (!trimmed || isStreaming || disabled) return;
    onSend(trimmed);
    onDraftChange('');
    textareaRef.current?.focus();
  }, [draft, isStreaming, disabled, onSend, onDraftChange]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== 'Enter') return;
      // isComposing guards IME input: committing a Japanese/Chinese candidate
      // fires Enter and must not send the message.
      if (event.nativeEvent.isComposing) return;
      if (!sendOnEnter) return;
      if (event.shiftKey) return;

      event.preventDefault();
      submit();
    },
    [sendOnEnter, submit],
  );

  return (
    <div className="composer">
      <div className="composer-inner">
        <textarea
          ref={textareaRef}
          className="composer-input"
          data-testid="composer-input"
          value={draft}
          rows={rows}
          disabled={disabled}
          placeholder={disabled ? 'Ollama is not connected' : 'Send a message…'}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={handleKeyDown}
          autoFocus
        />

        {isStreaming ? (
          <button type="button" className="stop-button" onClick={onStop} data-testid="stop-button">
            Stop
          </button>
        ) : (
          <button
            type="button"
            className="send-button"
            onClick={submit}
            disabled={!draft.trim() || disabled}
            data-testid="send-button"
          >
            Send
          </button>
        )}
      </div>
      <div className="composer-hint">
        {sendOnEnter ? 'Enter to send · Shift+Enter for a new line' : 'Click Send to submit'}
      </div>
    </div>
  );
});

export default Composer;
