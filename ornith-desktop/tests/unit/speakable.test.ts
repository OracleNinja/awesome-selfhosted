import { describe, expect, it } from 'vitest';
import { speakableText } from '../../shared/speakable';

describe('speakableText', () => {
  it('passes plain prose through', () => {
    expect(speakableText('Hello there, how are you?')).toBe('Hello there, how are you?');
  });

  it('announces a fenced code block instead of reciting it', () => {
    const out = speakableText('Here you go:\n\n```python\nprint("hi")\n```\n\nThat prints hi.');
    expect(out).toContain('(code block)');
    expect(out).not.toContain('print("hi")');
    expect(out).not.toContain('```');
    expect(out).toContain('That prints hi.');
  });

  it('handles an unterminated fence from a stopped stream', () => {
    const out = speakableText('Starting:\n\n```js\nconst a = 1;');
    expect(out).toContain('(code block)');
    expect(out).not.toContain('const a');
  });

  it('keeps inline code content but drops the backticks', () => {
    expect(speakableText('Run `npm test` now.')).toBe('Run npm test now.');
  });

  it('reads link text, never the URL', () => {
    expect(speakableText('See [the docs](https://example.com/a/b) for details.')).toBe(
      'See the docs for details.',
    );
  });

  it('replaces a bare URL rather than spelling it out', () => {
    const out = speakableText('Go to https://example.com/very/long/path now');
    expect(out).not.toContain('example.com');
    expect(out).toContain('link');
  });

  it('collapses a table to a single announcement', () => {
    const out = speakableText('Results:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\nDone.');
    expect(out).toContain('(table)');
    expect((out.match(/\(table\)/g) ?? []).length).toBe(1);
    expect(out).toContain('Done.');
  });

  it('strips headings, emphasis, quotes and bullets', () => {
    const out = speakableText('## Title\n\n**bold** and *italic* and ~~gone~~\n\n> quoted\n\n- one\n- two');
    expect(out).not.toMatch(/[#*>~]/);
    expect(out).toContain('bold');
    expect(out).toContain('italic');
    expect(out).toContain('quoted');
    expect(out).toContain('one');
  });

  it('turns paragraph breaks into sentence pauses', () => {
    expect(speakableText('First para.\n\nSecond para.')).toBe('First para. Second para.');
  });

  it('does not produce doubled full stops', () => {
    expect(speakableText('Done.\n\nNext.')).not.toContain('..');
  });

  it('returns an empty string for content with nothing speakable', () => {
    expect(speakableText('```\ncode only\n```').replace(/\(code block\)/, '').trim()).toBe('');
  });

  it('truncates very long text on a sentence boundary', () => {
    const long = 'This is a sentence. '.repeat(500);
    const out = speakableText(long, 200);
    expect(out.length).toBeLessThanOrEqual(200);
    expect(out.endsWith('.')).toBe(true);
  });

  it('truncates mid-text without leaving a dangling space', () => {
    const out = speakableText('x'.repeat(500), 100);
    expect(out.length).toBeLessThanOrEqual(100);
    expect(out).toBe(out.trimEnd());
  });

  it('handles an empty input', () => {
    expect(speakableText('')).toBe('');
  });
});
