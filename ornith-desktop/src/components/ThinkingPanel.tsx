import { useState } from 'react';

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
  // null = the user has not taken manual control. While that holds, openness
  // is derived fresh from props on every render, so a prop that changes after
  // mount (most notably `incomplete`, which only becomes true at finalise —
  // see MessageItem/orchestrator) is reflected immediately instead of being
  // frozen at whatever it was when the panel first mounted (P2Q-DEFECT-1).
  // Once the user toggles, their choice wins over anything that arrives
  // afterward, including an `incomplete` flag landing later.
  const [userOverride, setUserOverride] = useState<boolean | null>(null);

  // Auto-open whenever the block turned out incomplete (stays open forever,
  // it never becomes non-incomplete again) or whenever the caller wants
  // reasoning shown by default — deliberately not gated on `answerStarted`,
  // since "always expand reasoning" means exactly that.
  const autoOpen = incomplete || defaultOpen;
  const open = userOverride ?? autoOpen;

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
        onClick={() => setUserOverride(!open)}
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
