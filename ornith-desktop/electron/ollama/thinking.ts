/**
 * Fallback reasoning parser, used only when the model does not expose the
 * structured `message.thinking` field (SPEC §7.3).
 *
 * This is a state machine rather than a regex because `<think>` routinely
 * arrives split across chunk boundaries — "<thi" then "nk>" — and a regex run
 * over each chunk would never match it.
 *
 * Pure: no I/O, no timers.
 */

const OPEN = '<think>';
const CLOSE = '</think>';
/** Longest delimiter, so text is never withheld indefinitely. */
const MAX_HOLD = CLOSE.length;

export type ThinkState = 'answer' | 'thinking';

export interface ThinkingSplit {
  content: string;
  thinking: string;
}

export interface ThinkingFlush extends ThinkingSplit {
  /** True when the stream ended inside an unclosed <think> block. */
  malformed: boolean;
}

export interface ThinkingParser {
  push(text: string): ThinkingSplit;
  flush(): ThinkingFlush;
  readonly state: ThinkState;
}

/**
 * Length of the longest suffix of `s` that is a proper prefix of a delimiter.
 * That suffix must be held back until more text arrives to disambiguate it.
 */
function heldBackLength(s: string): number {
  const max = Math.min(s.length, MAX_HOLD - 1);
  for (let k = max; k > 0; k -= 1) {
    const tail = s.slice(-k);
    if (OPEN.startsWith(tail) || CLOSE.startsWith(tail)) return k;
  }
  return 0;
}

/** Earliest index of either delimiter, or -1. */
function findNext(s: string, from: number): { index: number; tag: string } | null {
  const open = s.indexOf(OPEN, from);
  const close = s.indexOf(CLOSE, from);

  if (open === -1 && close === -1) return null;
  if (open === -1) return { index: close, tag: CLOSE };
  if (close === -1) return { index: open, tag: OPEN };
  // A '<think>' can never start at the same index as '</think>'.
  return open < close ? { index: open, tag: OPEN } : { index: close, tag: CLOSE };
}

export function createThinkingParser(): ThinkingParser {
  let state: ThinkState = 'answer';
  let pending = '';

  function consume(final: boolean): ThinkingSplit {
    let content = '';
    let thinking = '';

    for (;;) {
      const hit = findNext(pending, 0);
      if (!hit) break;

      const before = pending.slice(0, hit.index);
      if (state === 'answer') content += before;
      else thinking += before;

      pending = pending.slice(hit.index + hit.tag.length);

      if (hit.tag === OPEN) {
        // Nested opener while already thinking: ignore it, stay in thinking.
        if (state === 'answer') state = 'thinking';
      } else {
        // Stray closer while answering: ignore the tag, keep answering.
        if (state === 'thinking') state = 'answer';
      }
    }

    // No complete delimiter left. Emit everything except a possible partial tag.
    const hold = final ? 0 : heldBackLength(pending);
    const emit = hold > 0 ? pending.slice(0, pending.length - hold) : pending;
    pending = hold > 0 ? pending.slice(pending.length - hold) : '';

    if (state === 'answer') content += emit;
    else thinking += emit;

    return { content, thinking };
  }

  return {
    push(text: string): ThinkingSplit {
      if (!text) return { content: '', thinking: '' };
      pending += text;
      return consume(false);
    },

    flush(): ThinkingFlush {
      const split = consume(true);
      // Unclosed block: keep the text, tell the caller it was incomplete.
      // Discarding the user's reasoning output would be worse than showing it.
      return { ...split, malformed: state === 'thinking' };
    },

    get state() {
      return state;
    },
  };
}
