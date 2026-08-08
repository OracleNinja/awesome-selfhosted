import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createCoalescer, type Delta } from '../../electron/ollama/coalescer';

describe('createCoalescer', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function harness(maxBytes = 2048) {
    const batches: Delta[] = [];
    const c = createCoalescer({
      intervalMs: 50,
      maxBytes,
      onFlush: (b) => batches.push(b),
    });
    return { c, batches };
  }

  it('emits nothing before the interval elapses', () => {
    const { c, batches } = harness();
    c.push({ content: 'a', thinking: '' });
    vi.advanceTimersByTime(49);
    expect(batches).toHaveLength(0);
  });

  it('batches many pushes in one window into a single emission', () => {
    const { c, batches } = harness();
    for (let i = 0; i < 20; i += 1) c.push({ content: 'x', thinking: '' });
    vi.advanceTimersByTime(50);
    expect(batches).toEqual([{ content: 'x'.repeat(20), thinking: '' }]);
  });

  it('keeps content and thinking in separate accumulators', () => {
    const { c, batches } = harness();
    c.push({ content: 'a', thinking: 'T' });
    c.push({ content: 'b', thinking: 'U' });
    vi.advanceTimersByTime(50);
    expect(batches).toEqual([{ content: 'ab', thinking: 'TU' }]);
  });

  it('flushes early once maxBytes is reached', () => {
    const { c, batches } = harness(10);
    c.push({ content: 'x'.repeat(10), thinking: '' });
    expect(batches).toHaveLength(1); // no timer advance needed
  });

  it('emits multiple batches across multiple windows', () => {
    const { c, batches } = harness();
    c.push({ content: 'a', thinking: '' });
    vi.advanceTimersByTime(50);
    c.push({ content: 'b', thinking: '' });
    vi.advanceTimersByTime(50);
    expect(batches).toEqual([
      { content: 'a', thinking: '' },
      { content: 'b', thinking: '' },
    ]);
  });

  it('never emits an empty batch', () => {
    const { c, batches } = harness();
    c.push({ content: '', thinking: '' });
    vi.advanceTimersByTime(200);
    c.flush();
    expect(batches).toHaveLength(0);
  });

  it('manual flush emits immediately and cancels the pending timer', () => {
    const { c, batches } = harness();
    c.push({ content: 'a', thinking: '' });
    c.flush();
    expect(batches).toHaveLength(1);
    vi.advanceTimersByTime(200);
    expect(batches).toHaveLength(1);
  });

  it('loses no characters across a long run', () => {
    const { c, batches } = harness();
    let expected = '';
    for (let i = 0; i < 500; i += 1) {
      const tok = `t${i} `;
      expected += tok;
      c.push({ content: tok, thinking: '' });
      if (i % 7 === 0) vi.advanceTimersByTime(50);
    }
    c.flush();
    expect(batches.map((b) => b.content).join('')).toBe(expected);
  });

  it('dispose drops buffered text and cancels the timer', () => {
    const { c, batches } = harness();
    c.push({ content: 'a', thinking: '' });
    c.dispose();
    vi.advanceTimersByTime(200);
    expect(batches).toHaveLength(0);
  });
});
