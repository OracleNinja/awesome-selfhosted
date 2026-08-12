import { useEffect, useState } from 'react';

interface Props {
  thinking: string;
  /** True while this message is still generating. */
  isStreaming: boolean;
  /** True once the answer has started, which auto-collapses the panel. */
  answerStarted: boolean;
  defaultOpen: boolean;
  incomplete?: boolean;
}

/**
 * Reasoning is shown above the answer as a quiet, collapsed surface. The answer
 * always stays the visually dominant element (SPEC §7.4).
 */
export default function ThinkingPanel({
  thinking,
  isStreaming,
  answerStarted,
  defaultOpen,
  incomplete,
}: Props) {
  const [open, setOpen] = useState(defaultOpen || incomplete);
  const [userToggled, setUserToggled] = useState(false);

  // Collapse automatically once the answer begins, unless the user has taken over.
  useEffect(() => {
    if (!userToggled && answerStarted && !defaultOpen && !incomplete) setOpen(false);
  }, [answerStarted, userToggled, defaultOpen, incomplete]);

  if (!thinking) return null;

  const label = incomplete
    ? 'Incomplete reasoning'
    : isStreaming && !answerStarted
      ? `Thinking… (${thinking.length} chars)`
      : 'Thinking';

  return (
    <div className={`thinking-panel${incomplete ? ' is-incomplete' : ''}`}>
      <button
        type="button"
        className="thinking-toggle"
        aria-expanded={open}
        data-testid="thinking-toggle"
        onClick={() => {
          setUserToggled(true);
          setOpen((v) => !v);
        }}
      >
        <span className={`thinking-caret${open ? ' is-open' : ''}`} aria-hidden="true">
          ▸
        </span>
        {label}
      </button>

      {open ? (
        <div className="thinking-body" data-testid="thinking-body">
          {thinking}
        </div>
      ) : null}
    </div>
  );
}
