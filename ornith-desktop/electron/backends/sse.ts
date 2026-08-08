/**
 * Server-Sent Events frame parser.
 *
 * Same discipline as the NDJSON parser: a chunk boundary can fall anywhere,
 * including mid-frame and mid-multibyte-character. Pure, so the awkward cases
 * are tested without a network.
 */

export interface SseFrame {
  event: string;
  data: string;
}

export interface SseParser {
  push(chunk: Uint8Array): SseFrame[];
  flush(): SseFrame[];
}

function parseBlock(block: string): SseFrame | null {
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue; // blank or comment/keepalive
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    // A single leading space after the colon is part of the framing, not data.
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');

    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }

  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

export function createSseParser(): SseParser {
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  function drain(final: boolean): SseFrame[] {
    // Frames are separated by a blank line; normalise CRLF first.
    const normalised = buffer.replace(/\r\n/g, '\n');
    const blocks = normalised.split('\n\n');
    buffer = final ? '' : (blocks.pop() ?? '');

    const frames: SseFrame[] = [];
    for (const block of blocks) {
      const frame = parseBlock(block);
      if (frame) frames.push(frame);
    }
    return frames;
  }

  return {
    push(chunk: Uint8Array): SseFrame[] {
      buffer += decoder.decode(chunk, { stream: true });
      return drain(false);
    },

    flush(): SseFrame[] {
      buffer += decoder.decode();
      return drain(true);
    },
  };
}
