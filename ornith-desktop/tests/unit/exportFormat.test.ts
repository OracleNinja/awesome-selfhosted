import { describe, expect, it } from 'vitest';
import { formatConversation, suggestFilename } from '../../shared/exportFormat';
import type { ChatMessage, Conversation } from '../../shared/types';

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-1',
    seq: 0,
    role: 'user',
    content: 'Hello',
    thinking: '',
    status: 'complete',
    createdAt: 1700000000000,
    ...overrides,
  };
}

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'conv-1',
    title: 'Test conversation',
    model: 'llama3:latest',
    createdAt: 1700000000000,
    updatedAt: 1700000100000,
    messages: [],
    ...overrides,
  };
}

describe('formatConversation (markdown)', () => {
  it('includes the title, model and ISO timestamps', () => {
    const conversation = makeConversation({ title: 'My Chat', model: 'llama3:latest' });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).toContain('# My Chat');
    expect(out).toContain('llama3:latest');
    expect(out).toContain(new Date(conversation.createdAt).toISOString());
    expect(out).toContain(new Date(conversation.updatedAt).toISOString());
  });

  it('falls back to a default title when the title is empty or whitespace', () => {
    const conversation = makeConversation({ title: '   ' });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).toContain('# Untitled conversation');
  });

  it('notes when a conversation has no messages, without throwing', () => {
    const conversation = makeConversation({ messages: [] });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).toContain('no messages');
  });

  it('attributes each message to its role, in order', () => {
    const conversation = makeConversation({
      messages: [
        makeMessage({ id: 'a', seq: 0, role: 'user', content: 'First question' }),
        makeMessage({ id: 'b', seq: 1, role: 'assistant', content: 'First answer' }),
        makeMessage({ id: 'c', seq: 2, role: 'user', content: 'Second question' }),
      ],
    });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    const userHeadingIdx = out.indexOf('## User');
    const firstQuestionIdx = out.indexOf('First question');
    const assistantHeadingIdx = out.indexOf('## Assistant');
    const firstAnswerIdx = out.indexOf('First answer');
    const secondQuestionIdx = out.lastIndexOf('Second question');

    expect(userHeadingIdx).toBeGreaterThanOrEqual(0);
    expect(userHeadingIdx).toBeLessThan(firstQuestionIdx);
    expect(firstQuestionIdx).toBeLessThan(assistantHeadingIdx);
    expect(assistantHeadingIdx).toBeLessThan(firstAnswerIdx);
    expect(firstAnswerIdx).toBeLessThan(secondQuestionIdx);
  });

  it('separates reasoning from the response and labels each clearly', () => {
    const conversation = makeConversation({
      messages: [
        makeMessage({
          role: 'assistant',
          thinking: 'Let me work this out step by step.',
          content: 'The answer is 42.',
        }),
      ],
    });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: true });

    expect(out).toContain('**Reasoning**');
    expect(out).toContain('**Response**');
    expect(out).toContain('> Let me work this out step by step.');
    expect(out).toContain('The answer is 42.');

    const reasoningIdx = out.indexOf('**Reasoning**');
    const responseLabelIdx = out.indexOf('**Response**');
    const answerIdx = out.indexOf('The answer is 42.');

    expect(reasoningIdx).toBeLessThan(responseLabelIdx);
    expect(responseLabelIdx).toBeLessThan(answerIdx);
  });

  it('omits reasoning entirely, including its text, when includeReasoning is false', () => {
    const conversation = makeConversation({
      messages: [
        makeMessage({
          role: 'assistant',
          thinking: 'Secret scratch work.',
          content: 'The public answer.',
        }),
      ],
    });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).not.toContain('**Reasoning**');
    expect(out).not.toContain('Secret scratch work.');
    expect(out).toContain('The public answer.');
  });

  it('does not add reasoning labels when a message has no reasoning to show', () => {
    const conversation = makeConversation({
      messages: [makeMessage({ role: 'assistant', thinking: '', content: 'Just an answer.' })],
    });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: true });

    expect(out).not.toContain('**Reasoning**');
    expect(out).toContain('Just an answer.');
  });

  it('leaves a heading-like line inside a fenced code block untouched', () => {
    const content = ['Here:', '', '```py', '# this is a comment, not a heading', '```'].join('\n');
    const conversation = makeConversation({ messages: [makeMessage({ content })] });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).toContain('# this is a comment, not a heading');
    expect(out).not.toContain('\\# this is a comment');
  });

  it('escapes a heading-like line outside a fenced code block', () => {
    const content = 'Some text.\n\n# Not a real section\n\nMore text.';
    const conversation = makeConversation({ messages: [makeMessage({ content })] });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).toContain('\\# Not a real section');
  });

  it('closes an unterminated fence so a later message is not swallowed into a code block', () => {
    const unterminated = 'Starting:\n\n```js\nconst a = 1;';
    const conversation = makeConversation({
      messages: [
        makeMessage({ id: 'a', role: 'assistant', status: 'cancelled', content: unterminated }),
        makeMessage({ id: 'b', role: 'user', content: 'Are you still there?' }),
      ],
    });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    const fenceCount = (out.match(/```/g) ?? []).length;
    expect(fenceCount % 2).toBe(0);
    expect(out).toContain('## User');
    expect(out).toContain('Are you still there?');
  });

  it('notes a cancelled message', () => {
    const conversation = makeConversation({
      messages: [makeMessage({ status: 'cancelled', content: 'Partial ans' })],
    });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).toContain('stopped before completion');
  });

  it('shows the error message but never the internal error detail', () => {
    const conversation = makeConversation({
      messages: [
        makeMessage({
          status: 'error',
          content: '',
          error: {
            code: 'UNKNOWN',
            message: 'Something went wrong.',
            detail: 'stack trace at internal-module.ts:42',
          },
        }),
      ],
    });
    const out = formatConversation(conversation, { format: 'markdown', includeReasoning: false });

    expect(out).toContain('Something went wrong.');
    expect(out).not.toContain('internal-module.ts');
  });
});

describe('formatConversation (json)', () => {
  it('parses back with JSON.parse and keeps timestamps as numbers', () => {
    const conversation = makeConversation();
    const out = formatConversation(conversation, { format: 'json', includeReasoning: false });
    const parsed = JSON.parse(out);

    expect(parsed.id).toBe(conversation.id);
    expect(parsed.title).toBe(conversation.title);
    expect(typeof parsed.createdAt).toBe('number');
    expect(typeof parsed.updatedAt).toBe('number');
  });

  it('produces an empty messages array for a conversation with no messages', () => {
    const conversation = makeConversation({ messages: [] });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: false });
    const parsed = JSON.parse(out);

    expect(parsed.messages).toEqual([]);
  });

  it('preserves message order', () => {
    const conversation = makeConversation({
      messages: [makeMessage({ id: 'a', seq: 0 }), makeMessage({ id: 'b', seq: 1 })],
    });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: false });
    const parsed = JSON.parse(out);

    expect(parsed.messages.map((message: { id: string }) => message.id)).toEqual(['a', 'b']);
  });

  it('omits the thinking key entirely when includeReasoning is false', () => {
    const conversation = makeConversation({
      messages: [makeMessage({ thinking: 'Reasoning text.' })],
    });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: false });
    const parsed = JSON.parse(out);

    expect(Object.prototype.hasOwnProperty.call(parsed.messages[0], 'thinking')).toBe(false);
  });

  it('includes the thinking key even when empty, when includeReasoning is true', () => {
    const conversation = makeConversation({ messages: [makeMessage({ thinking: '' })] });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: true });
    const parsed = JSON.parse(out);

    expect(Object.prototype.hasOwnProperty.call(parsed.messages[0], 'thinking')).toBe(true);
    expect(parsed.messages[0].thinking).toBe('');
  });

  it('includes non-empty thinking text when includeReasoning is true', () => {
    const conversation = makeConversation({
      messages: [makeMessage({ thinking: 'Reasoning text.' })],
    });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: true });
    const parsed = JSON.parse(out);

    expect(parsed.messages[0].thinking).toBe('Reasoning text.');
  });

  it('omits error and stats when absent, includes them when present', () => {
    const conversation = makeConversation({
      messages: [
        makeMessage({ id: 'no-extras' }),
        makeMessage({
          id: 'with-extras',
          stats: {
            evalCount: 10,
            evalDurationNs: 1000,
            promptEvalCount: 5,
            totalDurationNs: 2000,
            tokensPerSecond: 12.5,
          },
        }),
      ],
    });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: false });
    const parsed = JSON.parse(out);

    expect(Object.prototype.hasOwnProperty.call(parsed.messages[0], 'error')).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(parsed.messages[0], 'stats')).toBe(false);
    expect(parsed.messages[1].stats.tokensPerSecond).toBe(12.5);
  });

  it('keeps the error message but drops the internal detail field', () => {
    const conversation = makeConversation({
      messages: [
        makeMessage({
          status: 'error',
          error: { code: 'UNKNOWN', message: 'Boom.', detail: 'internal-secret-path' },
        }),
      ],
    });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: false });
    const parsed = JSON.parse(out);

    expect(parsed.messages[0].error.message).toBe('Boom.');
    expect(Object.prototype.hasOwnProperty.call(parsed.messages[0].error, 'detail')).toBe(false);
    expect(out).not.toContain('internal-secret-path');
  });

  it('round-trips message content containing quotes, backslashes and newlines exactly', () => {
    const trickyContent = 'Quote: "hi"\nBackslash: \\\nDone.';
    const conversation = makeConversation({ messages: [makeMessage({ content: trickyContent })] });
    const out = formatConversation(conversation, { format: 'json', includeReasoning: false });
    const parsed = JSON.parse(out);

    expect(parsed.messages[0].content).toBe(trickyContent);
  });
});

describe('suggestFilename', () => {
  it('appends .md for markdown and .json for json', () => {
    expect(suggestFilename('My Chat', 'markdown')).toBe('My Chat.md');
    expect(suggestFilename('My Chat', 'json')).toBe('My Chat.json');
  });

  it('falls back to a default name for an empty or whitespace-only title', () => {
    expect(suggestFilename('', 'markdown')).toBe('conversation.md');
    expect(suggestFilename('   ', 'json')).toBe('conversation.json');
  });

  it('falls back to a default name for a title made only of dots', () => {
    expect(suggestFilename('...', 'markdown')).toBe('conversation.md');
  });

  it('never leaves a path separator in the result', () => {
    const hostileTitles = ['a/b\\c', '../../etc/passwd', 'C:\\Windows\\System32', '/etc/shadow'];
    for (const title of hostileTitles) {
      const result = suggestFilename(title, 'markdown');
      expect(result).not.toMatch(/[\\/]/);
      expect(result.length).toBeGreaterThan(0);
    }
  });

  it('strips control characters, including an embedded NUL, without throwing', () => {
    const withControlChars = `bad${String.fromCharCode(0)}name${String.fromCharCode(7)}here`;
    const result = suggestFilename(withControlChars, 'markdown');

    expect(result).not.toContain(String.fromCharCode(0));
    expect(result).not.toContain(String.fromCharCode(7));
    expect(result.endsWith('.md')).toBe(true);
  });

  it('never produces a name that starts with a dot', () => {
    expect(suggestFilename('.hidden', 'markdown').startsWith('.')).toBe(false);
    expect(suggestFilename('....hidden', 'markdown').startsWith('.')).toBe(false);
  });

  it('does not double an extension that already matches the target format', () => {
    expect(suggestFilename('Notes.md', 'markdown')).toBe('Notes.md');
    expect(suggestFilename('Notes.json', 'json')).toBe('Notes.json');
  });

  it('keeps a mismatched extension alongside the new one', () => {
    expect(suggestFilename('Notes.json', 'markdown')).toBe('Notes.json.md');
  });

  it('avoids a bare Windows reserved device name', () => {
    const stem = suggestFilename('CON', 'markdown').split('.')[0];
    expect(stem.toUpperCase()).not.toBe('CON');
  });

  it('avoids a reserved device name even with an extension already in the title', () => {
    const stem = suggestFilename('NUL.txt', 'json').split('.')[0];
    expect(stem.toUpperCase()).not.toBe('NUL');
  });

  it('is case-insensitive when detecting a reserved device name', () => {
    const stem = suggestFilename('con', 'markdown').split('.')[0];
    expect(stem.toUpperCase()).not.toBe('CON');
  });

  it('bounds an absurdly long ASCII title to a sane byte length', () => {
    const hugeTitle = 'a'.repeat(10000);
    const result = suggestFilename(hugeTitle, 'markdown');

    expect(new TextEncoder().encode(result).length).toBeLessThanOrEqual(255);
    expect(result.endsWith('.md')).toBe(true);
  });

  it('bounds an absurdly long multi-byte title without splitting a surrogate pair', () => {
    const emoji = String.fromCodePoint(0x1f600);
    const hugeTitle = emoji.repeat(2000);
    const result = suggestFilename(hugeTitle, 'json');

    expect(new TextEncoder().encode(result).length).toBeLessThanOrEqual(255);

    // Every remaining code point in the base must still be the whole emoji,
    // never a lone surrogate half turned into a replacement character.
    const base = result.slice(0, result.length - '.json'.length);
    for (const char of base) {
      expect(char).toBe(emoji);
    }
  });

  it('is a pure function: same input always produces the same output', () => {
    expect(suggestFilename('Repeatable Title', 'markdown')).toBe(
      suggestFilename('Repeatable Title', 'markdown'),
    );
  });
});
