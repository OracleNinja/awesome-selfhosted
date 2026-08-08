import { describe, expect, it } from 'vitest';
import { createSseParser } from '../../electron/backends/sse';

const enc = (s: string) => new TextEncoder().encode(s);

describe('createSseParser', () => {
  it('parses a complete frame', () => {
    const p = createSseParser();
    expect(p.push(enc('event: delta\ndata: {"a":1}\n\n'))).toEqual([
      { event: 'delta', data: '{"a":1}' },
    ]);
  });

  it('defaults the event name to message', () => {
    const p = createSseParser();
    expect(p.push(enc('data: hello\n\n'))).toEqual([{ event: 'message', data: 'hello' }]);
  });

  it('holds a partial frame until the blank line arrives', () => {
    const p = createSseParser();
    expect(p.push(enc('event: delta\ndata: {"a":1}\n'))).toEqual([]);
    expect(p.push(enc('\n'))).toEqual([{ event: 'delta', data: '{"a":1}' }]);
  });

  // The same discipline the NDJSON parser needs: boundaries fall anywhere.
  it('reassembles a frame split across three chunks', () => {
    const p = createSseParser();
    expect(p.push(enc('event: de'))).toEqual([]);
    expect(p.push(enc('lta\ndata: {"conte'))).toEqual([]);
    expect(p.push(enc('nt":"hi"}\n\n'))).toEqual([{ event: 'delta', data: '{"content":"hi"}' }]);
  });

  it('survives a multibyte character split across chunks', () => {
    const bytes = enc('data: {"content":"→"}\n\n');
    const cut = bytes.indexOf(0xe2) + 1;
    const p = createSseParser();
    p.push(bytes.slice(0, cut));
    expect(p.push(bytes.slice(cut))).toEqual([{ event: 'message', data: '{"content":"→"}' }]);
  });

  it('parses several frames in one chunk, in order', () => {
    const p = createSseParser();
    const frames = p.push(enc('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n'));
    expect(frames.map((f) => f.event)).toEqual(['a', 'b']);
  });

  it('joins multi-line data fields with newlines', () => {
    const p = createSseParser();
    expect(p.push(enc('data: line1\ndata: line2\n\n'))).toEqual([
      { event: 'message', data: 'line1\nline2' },
    ]);
  });

  it('ignores comment/keepalive lines', () => {
    const p = createSseParser();
    expect(p.push(enc(': keepalive\n\ndata: real\n\n'))).toEqual([
      { event: 'message', data: 'real' },
    ]);
  });

  it('normalises CRLF framing', () => {
    const p = createSseParser();
    expect(p.push(enc('event: delta\r\ndata: x\r\n\r\n'))).toEqual([
      { event: 'delta', data: 'x' },
    ]);
  });

  it('strips exactly one leading space after the colon', () => {
    const p = createSseParser();
    expect(p.push(enc('data:  two-spaces\n\n'))[0].data).toBe(' two-spaces');
  });

  it('flush drains a trailing frame with no terminating blank line', () => {
    const p = createSseParser();
    expect(p.push(enc('event: done\ndata: {}'))).toEqual([]);
    expect(p.flush()).toEqual([{ event: 'done', data: '{}' }]);
  });

  it('emits nothing for a frame with no data field', () => {
    const p = createSseParser();
    expect(p.push(enc('event: ping\n\n'))).toEqual([]);
  });
});
