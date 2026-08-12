import { describe, expect, it } from 'vitest';
import { createNdjsonParser } from '../../electron/ollama/ndjson';

const enc = (s: string) => new TextEncoder().encode(s);

describe('createNdjsonParser', () => {
  it('parses one object per line', () => {
    const p = createNdjsonParser();
    const out = p.push(enc('{"a":1}\n{"a":2}\n'));
    expect(out).toEqual([{ a: 1 }, { a: 2 }]);
  });

  it('holds a partial line until the newline arrives', () => {
    const p = createNdjsonParser();
    expect(p.push(enc('{"a":'))).toEqual([]);
    expect(p.push(enc('1}\n'))).toEqual([{ a: 1 }]);
  });

  it('reassembles an object split across three chunks', () => {
    const p = createNdjsonParser();
    expect(p.push(enc('{"mess'))).toEqual([]);
    expect(p.push(enc('age":{"con'))).toEqual([]);
    expect(p.push(enc('tent":"hi"}}\n'))).toEqual([{ message: { content: 'hi' } }]);
  });

  it('survives a multibyte character split across chunks', () => {
    // '→' is E2 86 92; split it down the middle.
    const bytes = enc('{"message":{"content":"→"}}\n');
    const cut = bytes.indexOf(0xe2) + 1;
    const p = createNdjsonParser();
    expect(p.push(bytes.slice(0, cut))).toEqual([]);
    const out = p.push(bytes.slice(cut));
    expect(out).toEqual([{ message: { content: '→' } }]);
  });

  it('skips blank lines', () => {
    const p = createNdjsonParser();
    expect(p.push(enc('\n\n{"a":1}\n\n'))).toEqual([{ a: 1 }]);
  });

  it('skips a malformed line without throwing and keeps going', () => {
    const p = createNdjsonParser();
    const out = p.push(enc('{"a":1}\nnot json at all\n{"a":2}\n'));
    expect(out).toEqual([{ a: 1 }, { a: 2 }]);
    expect(p.malformedCount).toBe(1);
  });

  it('surfaces an error payload as a normal chunk', () => {
    const p = createNdjsonParser();
    expect(p.push(enc('{"error":"model not found"}\n'))).toEqual([
      { error: 'model not found' },
    ]);
  });

  it('captures done stats', () => {
    const p = createNdjsonParser();
    const out = p.push(
      enc('{"done":true,"eval_count":42,"eval_duration":2000000000,"total_duration":3000000000}\n'),
    );
    expect(out[0]).toMatchObject({ done: true, eval_count: 42, eval_duration: 2_000_000_000 });
  });

  it('flush drains a trailing line with no newline', () => {
    const p = createNdjsonParser();
    expect(p.push(enc('{"a":1}'))).toEqual([]);
    expect(p.flush()).toEqual([{ a: 1 }]);
  });

  it('flush on an empty buffer returns nothing', () => {
    const p = createNdjsonParser();
    p.push(enc('{"a":1}\n'));
    expect(p.flush()).toEqual([]);
  });
});
