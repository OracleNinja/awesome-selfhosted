import { describe, expect, it } from 'vitest';
import { createThinkingParser } from '../../electron/ollama/thinking';

/** Feeds text in fixed-size slices to simulate arbitrary chunk boundaries. */
function pushChunked(text: string, size: number) {
  const p = createThinkingParser();
  let content = '';
  let thinking = '';
  for (let i = 0; i < text.length; i += size) {
    const out = p.push(text.slice(i, i + size));
    content += out.content;
    thinking += out.thinking;
  }
  const end = p.flush();
  return {
    content: content + end.content,
    thinking: thinking + end.thinking,
    malformed: end.malformed,
  };
}

describe('createThinkingParser', () => {
  it('treats untagged text as answer', () => {
    const p = createThinkingParser();
    expect(p.push('hello world')).toEqual({ content: 'hello world', thinking: '' });
    expect(p.flush().malformed).toBe(false);
  });

  it('routes a complete thinking block', () => {
    const r = pushChunked('<think>reasoning here</think>the answer', 1000);
    expect(r.thinking).toBe('reasoning here');
    expect(r.content).toBe('the answer');
    expect(r.malformed).toBe(false);
  });

  it('never emits the delimiters themselves', () => {
    const r = pushChunked('<think>a</think>b', 1000);
    expect(r.content).not.toContain('<think>');
    expect(r.content).not.toContain('</think>');
    expect(r.thinking).not.toContain('think>');
  });

  // The bug this whole module exists to prevent.
  it('handles an opening tag split as "<thi" + "nk>"', () => {
    const p = createThinkingParser();
    expect(p.push('<thi')).toEqual({ content: '', thinking: '' });
    const out = p.push('nk>secret');
    expect(out.content).toBe('');
    expect(out.thinking).toBe('secret');
  });

  it('handles a closing tag split across chunks', () => {
    const p = createThinkingParser();
    p.push('<think>abc');
    expect(p.push('</thi')).toEqual({ content: '', thinking: '' });
    const out = p.push('nk>visible');
    expect(out.content).toBe('visible');
  });

  it.each([1, 2, 3, 5, 7, 11])('produces the same result at chunk size %i', (size) => {
    const r = pushChunked('before<think>hidden reasoning</think>after', size);
    expect(r.content).toBe('beforeafter');
    expect(r.thinking).toBe('hidden reasoning');
    expect(r.malformed).toBe(false);
  });

  it('marks an unclosed block malformed but keeps the text', () => {
    const r = pushChunked('<think>never closed', 1000);
    expect(r.malformed).toBe(true);
    expect(r.thinking).toBe('never closed');
    expect(r.content).toBe('');
  });

  it('ignores a stray closing tag with no opener', () => {
    const r = pushChunked('answer</think>more', 1000);
    expect(r.content).toBe('answermore');
    expect(r.thinking).toBe('');
    expect(r.malformed).toBe(false);
  });

  it('ignores a nested opener; the first closer wins', () => {
    const r = pushChunked('<think>a<think>b</think>c', 1000);
    expect(r.thinking).toBe('ab');
    expect(r.content).toBe('c');
  });

  it('emits a trailing partial tag as literal text at flush', () => {
    const r = pushChunked('done<thi', 1000);
    expect(r.content).toBe('done<thi');
    expect(r.malformed).toBe(false);
  });

  it('never withholds more than 7 characters', () => {
    const p = createThinkingParser();
    // 200 chars of text ending in a non-delimiter char must pass straight through.
    const text = 'x'.repeat(200);
    expect(p.push(text).content).toBe(text);
  });

  it('handles a lone angle bracket in normal prose', () => {
    const r = pushChunked('if a < b and c > d then', 1000);
    expect(r.content).toBe('if a < b and c > d then');
    expect(r.thinking).toBe('');
  });

  it('handles multiple thinking blocks', () => {
    const r = pushChunked('<think>one</think>A<think>two</think>B', 1000);
    expect(r.thinking).toBe('onetwo');
    expect(r.content).toBe('AB');
  });

  it('reports its current state', () => {
    const p = createThinkingParser();
    expect(p.state).toBe('answer');
    p.push('<think>');
    expect(p.state).toBe('thinking');
    p.push('</think>');
    expect(p.state).toBe('answer');
  });
});
