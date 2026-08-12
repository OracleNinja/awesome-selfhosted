/**
 * Delta batching (SPEC C3).
 *
 * One IPC message per token is roughly 60-100 messages/second, each carrying
 * structured-clone overhead and each triggering a React render. Batching to a
 * ~50ms window cuts that by about 4x with no perceptible latency cost.
 */

export interface Delta {
  content: string;
  thinking: string;
}

export interface CoalescerOptions {
  intervalMs: number;
  /** Flush early once this much text has accumulated, so bursts stay snappy. */
  maxBytes: number;
  onFlush: (batch: Delta) => void;
}

export interface Coalescer {
  push(delta: Delta): void;
  /** Emit whatever is buffered right now. No-op when empty. */
  flush(): void;
  /** Cancel any pending timer and drop buffered text. */
  dispose(): void;
}

export function createCoalescer(options: CoalescerOptions): Coalescer {
  const { intervalMs, maxBytes, onFlush } = options;

  let content = '';
  let thinking = '';
  let timer: ReturnType<typeof setTimeout> | null = null;

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function flush(): void {
    clearTimer();
    if (!content && !thinking) return;
    const batch = { content, thinking };
    content = '';
    thinking = '';
    onFlush(batch);
  }

  return {
    push(delta: Delta): void {
      content += delta.content;
      thinking += delta.thinking;
      if (!content && !thinking) return;

      if (content.length + thinking.length >= maxBytes) {
        flush();
        return;
      }
      if (timer === null) {
        timer = setTimeout(flush, intervalMs);
      }
    },

    flush,

    dispose(): void {
      clearTimer();
      content = '';
      thinking = '';
    },
  };
}
