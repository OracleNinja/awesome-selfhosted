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

  /* ---- voice layer; all optional so the composer works without it ---- */
  voiceAvailable?: boolean;
  voiceUnavailableReason?: string;
  recordingState?: 'idle' | 'recording' | 'transcribing';
  onMicToggle?: () => void;
  voiceError?: string | null;
  onDismissVoiceError?: () => void;
  isSpeaking?: boolean;
  onStopSpeaking?: () => void;
}

export interface ComposerHandle {
  focus(): void;
}

const MAX_HEIGHT_PX = 200;

const Composer = forwardRef<ComposerHandle, Props>(function Composer(
  {
    onSend,
    onStop,
    isStreaming,
    disabled,
    sendOnEnter,
    draft,
    onDraftChange,
    voiceAvailable = false,
    voiceUnavailableReason,
    recordingState = 'idle',
    onMicToggle,
    voiceError,
    onDismissVoiceError,
    isSpeaking = false,
    onStopSpeaking,
  },
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
      {voiceError ? (
        <div className="voice-error" role="status" data-testid="voice-error">
          <span>{voiceError}</span>
          <button type="button" className="ghost-button ghost-button-small" onClick={onDismissVoiceError}>
            Dismiss
          </button>
        </div>
      ) : null}

      <div className="composer-inner">
        {onMicToggle ? (
          <button
            type="button"
            className={`mic-button is-${recordingState}`}
            onClick={onMicToggle}
            disabled={!voiceAvailable || disabled || isStreaming || recordingState === 'transcribing'}
            title={voiceAvailable ? 'Speak your message' : voiceUnavailableReason}
            aria-label={recordingState === 'recording' ? 'Stop recording' : 'Start recording'}
            aria-pressed={recordingState === 'recording'}
            data-testid="mic-button"
            data-state={recordingState}
          >
            {recordingState === 'transcribing' ? '···' : '●'}
          </button>
        ) : null}

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
        {recordingState === 'recording'
          ? 'Listening… click the dot again to send'
          : recordingState === 'transcribing'
            ? 'Transcribing on-device…'
            : sendOnEnter
              ? 'Enter to send · Shift+Enter for a new line'
              : 'Click Send to submit'}

        {isSpeaking && onStopSpeaking ? (
          <button
            type="button"
            className="stop-speaking-button"
            onClick={onStopSpeaking}
            data-testid="stop-speaking"
          >
            ◼ Stop speaking
          </button>
        ) : null}
      </div>
    </div>
  );
});

export default Composer;
