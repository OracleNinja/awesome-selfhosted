import { useCallback, useEffect, useRef, useState } from 'react';

export interface LiveText {
  content: string;
  thinking: string;
}

/**
 * Streaming accumulator (SPEC C3, renderer half).
 *
 * Deltas land in a ref and are committed to React state on an animation frame,
 * so several deltas arriving in one frame produce one render instead of one
 * render each. Without this, a long response re-parses its Markdown on every
 * token and the UI stops responding.
 */
export function useStreamBuffer() {
  const bufferRef = useRef<LiveText>({ content: '', thinking: '' });
  const frameRef = useRef<number | null>(null);
  const [live, setLive] = useState<LiveText>({ content: '', thinking: '' });

  const commit = useCallback(() => {
    frameRef.current = null;
    setLive({ ...bufferRef.current });
  }, []);

  const schedule = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(commit);
  }, [commit]);

  const append = useCallback(
    (delta: LiveText) => {
      bufferRef.current = {
        content: bufferRef.current.content + delta.content,
        thinking: bufferRef.current.thinking + delta.thinking,
      };
      schedule();
    },
    [schedule],
  );

  const reset = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    bufferRef.current = { content: '', thinking: '' };
    setLive({ content: '', thinking: '' });
  }, []);

  /** The authoritative buffer, including deltas not yet committed to state. */
  const read = useCallback(() => bufferRef.current, []);

  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    },
    [],
  );

  return { live, append, reset, read };
}
