import { describe, expect, it } from 'vitest';
import {
  deriveTitle,
  isModelInstalled,
  modelMatches,
  normaliseModelName,
  resolveActiveModel,
} from '../../shared/model';

describe('modelMatches (SPEC C4)', () => {
  it('matches a bare name against the :latest form Ollama reports', () => {
    expect(modelMatches('ornith-en:latest', 'ornith-en')).toBe(true);
    expect(modelMatches('ornith-en', 'ornith-en:latest')).toBe(true);
  });

  it('matches identical fully-qualified names', () => {
    expect(modelMatches('ornith-en:q4', 'ornith-en:q4')).toBe(true);
  });

  it('does not match different tags of the same model', () => {
    expect(modelMatches('ornith-en:q4', 'ornith-en:latest')).toBe(false);
    expect(modelMatches('ornith-en:q4', 'ornith-en')).toBe(false);
  });

  it('does not match different models', () => {
    expect(modelMatches('llama3.1:8b', 'ornith-en')).toBe(false);
  });

  it('is case sensitive, as Ollama is', () => {
    expect(modelMatches('Ornith-EN:latest', 'ornith-en')).toBe(false);
  });

  it('tolerates surrounding whitespace', () => {
    expect(normaliseModelName('  ornith-en  ')).toBe('ornith-en:latest');
  });

  it('is what a naive includes() gets wrong', () => {
    const installed = ['ornith-en:latest'];
    expect(installed.includes('ornith-en')).toBe(false); // the bug
    expect(isModelInstalled(installed, 'ornith-en')).toBe(true); // the fix
  });
});

describe('resolveActiveModel', () => {
  const installed = ['llama3.1:8b', 'ornith-en:latest'];

  it('keeps the configured model when installed', () => {
    expect(resolveActiveModel(installed, 'ornith-en', 'ornith-en')).toBe('ornith-en');
  });

  it('falls back to the default when the configured model is absent', () => {
    expect(resolveActiveModel(installed, 'missing-model', 'ornith-en')).toBe('ornith-en');
  });

  it('falls back to the first installed when neither is present', () => {
    expect(resolveActiveModel(['llama3.1:8b'], 'missing', 'also-missing')).toBe('llama3.1:8b');
  });

  it('returns the configured name unchanged when nothing is installed', () => {
    expect(resolveActiveModel([], 'ornith-en', 'ornith-en')).toBe('ornith-en');
  });
});

describe('deriveTitle', () => {
  it('uses short text as-is', () => {
    expect(deriveTitle('Write a Python hello world')).toBe('Write a Python hello world');
  });

  it('truncates long text on a word boundary', () => {
    const t = deriveTitle('a'.repeat(10) + ' ' + 'b'.repeat(60));
    expect(t.length).toBeLessThanOrEqual(43);
    expect(t.endsWith('…')).toBe(true);
  });

  it('collapses whitespace and newlines', () => {
    expect(deriveTitle('hello\n\n   world')).toBe('hello world');
  });

  it('strips markdown syntax', () => {
    expect(deriveTitle('## **Bold** heading')).toBe('Bold heading');
    expect(deriveTitle('see `code` here')).toBe('see code here');
    expect(deriveTitle('[link text](http://x.com)')).toBe('link text');
  });

  it('drops fenced code blocks', () => {
    expect(deriveTitle('fix this ```js\nconst a=1;\n``` please')).toBe('fix this please');
  });

  it('returns "New chat" for empty or whitespace-only input', () => {
    expect(deriveTitle('')).toBe('New chat');
    expect(deriveTitle('   \n  ')).toBe('New chat');
    expect(deriveTitle('```js\ncode\n```')).toBe('New chat');
  });

  it('does not split an emoji in half', () => {
    const t = deriveTitle('👨‍👩‍👧‍👦'.repeat(30));
    expect(t).not.toContain('�');
    expect([...t].length).toBeLessThanOrEqual(43);
  });

  it('handles CJK text', () => {
    expect(deriveTitle('帮我看看这个文件的内容')).toBe('帮我看看这个文件的内容');
  });
});
