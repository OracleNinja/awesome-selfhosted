/**
 * Ollama streams newline-delimited JSON. Chunk boundaries fall anywhere —
 * mid-object and mid-multibyte-character — so this parser buffers and only
 * emits whole, parseable lines.
 *
 * Pure: no I/O, no timers. Everything hard about streaming is tested here.
 */

export interface OllamaChatChunk {
  message?: {
    role?: string;
    content?: string;
    thinking?: string;
  };
  done?: boolean;
  done_reason?: string;
  error?: string;
  eval_count?: number;
  eval_duration?: number;
  prompt_eval_count?: number;
  total_duration?: number;
}

export interface NdjsonParser {
  /** Feed raw bytes; returns whatever complete objects became available. */
  push(chunk: Uint8Array): OllamaChatChunk[];
  /** Call once at stream end to drain any trailing partial line. */
  flush(): OllamaChatChunk[];
  /** Lines that failed to parse — diagnostic only. */
  readonly malformedCount: number;
}

function parseLines(lines: string[], onMalformed: () => void): OllamaChatChunk[] {
  const out: OllamaChatChunk[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue; // Blank lines and keepalive newlines are normal.
    try {
      out.push(JSON.parse(trimmed) as OllamaChatChunk);
    } catch {
      // A malformed line must never kill the stream.
      onMalformed();
    }
  }
  return out;
}

export function createNdjsonParser(): NdjsonParser {
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let malformed = 0;
  const bump = () => {
    malformed += 1;
  };

  return {
    push(chunk: Uint8Array): OllamaChatChunk[] {
      // stream:true keeps a split UTF-8 sequence intact across chunks.
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      return parseLines(lines, bump);
    },

    flush(): OllamaChatChunk[] {
      buffer += decoder.decode();
      const lines = buffer.split('\n');
      buffer = '';
      return parseLines(lines, bump);
    },

    get malformedCount() {
      return malformed;
    },
  };
}
