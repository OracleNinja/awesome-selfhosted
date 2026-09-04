/**
 * Conversation export — the pure formatting layer.
 *
 * This module has no knowledge of Electron, the filesystem, or IPC. It turns
 * a `Conversation` already held in memory into a string; everything about
 * *where* that string goes (a save dialog, a clipboard, a file on disk) is
 * someone else's problem, which is exactly what makes this pure and easy to
 * test exhaustively.
 *
 * FORMAT DECISIONS
 *
 * Markdown — built for a human, and built to survive being re-rendered by a
 * CommonMark-ish parser:
 *   - Timestamps are rendered as ISO-8601 UTC (`toISOString`), not a locale
 *     string, so the output is deterministic regardless of the machine that
 *     produced it or later reads it.
 *   - Messages are separated by a blank line, `---`, then a blank line. The
 *     blank line *before* `---` is not decorative: without it, a `---`
 *     immediately after a line of plain text is a *setext heading
 *     underline* in CommonMark, which silently turns the previous line into
 *     a heading. ATX headings (`## Role`) do not have this problem, but the
 *     divider does, so it always gets one.
 *   - A message's own content can contain a line starting with `#` (the
 *     model writing "# Section" as prose, or genuine generated Markdown).
 *     Left alone that renders as a real heading at the same visual weight as
 *     this document's own role headings, which defeats the point of having
 *     a structure at all. Any line-leading heading marker *outside* a
 *     fenced code block is therefore escaped (`\#`); *inside* a fence it is
 *     left completely alone, because a `#` in, say, a Python comment has no
 *     Markdown meaning there and escaping it would corrupt the literal code
 *     being shown.
 *   - A message can end mid-fence: a `status: 'cancelled'` or `'streaming'`
 *     message captured while the model was still inside a ``` block. An
 *     unterminated fence in the output would swallow every message that
 *     follows it into one giant code block when rendered, so any such fence
 *     is force-closed before the message ends.
 *   - Reasoning (`thinking`) is rendered as its own blockquote headed
 *     "**Reasoning**", followed by a "**Response**" label ahead of the
 *     actual answer — the two are never adjacent without that pair of
 *     labels, so a skimming reader cannot mistake scratch work for the
 *     answer. The labels only appear when there is reasoning to show; the
 *     common case (no reasoning, or `includeReasoning: false`) stays
 *     uncluttered.
 *
 * JSON — built to be re-parsed, not read:
 *   - The shape mirrors `Conversation` / `ChatMessage` field-for-field
 *     rather than inventing a friendlier schema, per the contract already
 *     documented on `ExportFormat` in `types.ts`.
 *   - Timestamps stay as the raw epoch-millisecond numbers `Conversation`
 *     already uses — turning them into strings here would just be a lossy
 *     round trip for any consumer that wants to do arithmetic on them.
 *   - `thinking` is included only when `includeReasoning` is true, and then
 *     unconditionally — even if it happens to be `''` — so its presence
 *     means something. When `includeReasoning` is false the key is omitted
 *     entirely (conditional spread, never set to `''`), so a consumer can
 *     tell "excluded by request" apart from "the model didn't think".
 *   - `error` / `stats` are included only when present on the message.
 *   - `error.detail` is deliberately dropped even when `error` is included:
 *     `AppError.detail` is documented on the type itself as "Logged only,
 *     never rendered." An export is exactly the kind of user-facing,
 *     shareable surface that comment is warning about, so this keeps that
 *     existing boundary instead of quietly punching a hole in it.
 *   - `JSON.stringify(_, null, 2)`: the indentation is purely cosmetic (it
 *     changes nothing about validity or shape) and makes a hand-inspected
 *     export file, or a diff between two exports, readable.
 *
 * Pure: no imports outside `./types`, no I/O, fully unit-tested.
 */

import type {
  AppError,
  AppErrorAction,
  AppErrorCode,
  ChatMessage,
  Conversation,
  ExportFormat,
  GenerationStats,
  MessageStatus,
  Role,
} from './types';

const ROLE_LABELS: Record<Role, string> = {
  user: 'User',
  assistant: 'Assistant',
  system: 'System',
};

export function formatConversation(
  conversation: Conversation,
  options: { format: ExportFormat; includeReasoning: boolean },
): string {
  return options.format === 'markdown'
    ? formatAsMarkdown(conversation, options.includeReasoning)
    : formatAsJson(conversation, options.includeReasoning);
}

/* ---------------------------------------------------------------------- */
/* Markdown                                                                 */
/* ---------------------------------------------------------------------- */

function formatAsMarkdown(conversation: Conversation, includeReasoning: boolean): string {
  const lines: string[] = [
    `# ${singleLine(conversation.title) || 'Untitled conversation'}`,
    '',
    `- Model: ${singleLine(conversation.model)}`,
    `- Created: ${new Date(conversation.createdAt).toISOString()}`,
    `- Updated: ${new Date(conversation.updatedAt).toISOString()}`,
  ];

  if (conversation.messages.length === 0) {
    lines.push('', '_This conversation has no messages._');
    return lines.join('\n');
  }

  for (const message of conversation.messages) {
    lines.push(
      '',
      '---',
      '',
      `## ${ROLE_LABELS[message.role]} · ${new Date(message.createdAt).toISOString()}`,
      '',
      ...renderMessageBody(message, includeReasoning),
    );
  }

  return lines.join('\n');
}

function renderMessageBody(message: ChatMessage, includeReasoning: boolean): string[] {
  const body: string[] = [];
  const hasReasoning = includeReasoning && message.thinking.trim().length > 0;

  if (hasReasoning) {
    body.push(
      '**Reasoning**',
      '',
      blockquote(markdownSafeBlock(message.thinking)),
      '',
      '**Response**',
      '',
    );
  }

  const content = markdownSafeBlock(message.content).trim();
  body.push(content.length > 0 ? content : '_(no content)_');

  const note = statusNote(message);
  if (note) body.push('', note);

  return body;
}

/** A human reading a shared transcript deserves to know a reply is not the full story. */
function statusNote(message: ChatMessage): string | null {
  switch (message.status) {
    case 'complete':
      return null;
    case 'cancelled':
      return '_(response stopped before completion)_';
    case 'streaming':
      return '_(response was still streaming when exported)_';
    case 'error':
      return message.error ? `_(error: ${singleLine(message.error.message)})_` : '_(error)_';
    default:
      return null;
  }
}

/** Collapses text to one line, for values embedded in a heading or a metadata bullet. */
function singleLine(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

/**
 * Prefixes every line with `> ` so multi-line, possibly-fenced content nests
 * inside a single Markdown blockquote instead of being cut short (a bare
 * blank line would otherwise close the blockquote early).
 */
function blockquote(text: string): string {
  return text
    .split('\n')
    .map((line) => (line.length > 0 ? `> ${line}` : '>'))
    .join('\n');
}

/**
 * Makes freeform message text safe to place directly in the exported
 * document: closes an unterminated fenced code block (so it cannot swallow
 * every message that follows it) and escapes a line-leading ATX heading
 * marker that sits outside a fence (so it cannot be mistaken for this
 * document's own structure). Text inside a fence is left completely
 * untouched, since it renders verbatim and has no Markdown meaning there.
 */
function markdownSafeBlock(text: string): string {
  const fenceRe = /^(`{3,}|~{3,})/;
  const headingRe = /^(\s{0,3})(#{1,6})(?=\s|$)/;

  let openFence: string | null = null;
  const lines = text.split('\n').map((line) => {
    const fenceMatch = fenceRe.exec(line.trimStart());
    if (fenceMatch) {
      if (openFence === null) {
        openFence = fenceMatch[1];
      } else if (fenceMatch[1][0] === openFence[0] && fenceMatch[1].length >= openFence.length) {
        openFence = null;
      }
      return line;
    }
    return openFence === null ? line.replace(headingRe, '$1\\$2') : line;
  });

  if (openFence !== null) lines.push(openFence);
  return lines.join('\n');
}

/* ---------------------------------------------------------------------- */
/* JSON                                                                     */
/* ---------------------------------------------------------------------- */

interface JsonError {
  code: AppErrorCode;
  message: string;
  action?: AppErrorAction;
}

interface JsonMessage {
  id: string;
  seq: number;
  role: Role;
  content: string;
  thinking?: string;
  status: MessageStatus;
  error?: JsonError;
  stats?: GenerationStats;
  createdAt: number;
}

interface JsonConversation {
  id: string;
  title: string;
  model: string;
  createdAt: number;
  updatedAt: number;
  messages: JsonMessage[];
}

function formatAsJson(conversation: Conversation, includeReasoning: boolean): string {
  const payload: JsonConversation = {
    id: conversation.id,
    title: conversation.title,
    model: conversation.model,
    createdAt: conversation.createdAt,
    updatedAt: conversation.updatedAt,
    messages: conversation.messages.map((message) => toJsonMessage(message, includeReasoning)),
  };
  return JSON.stringify(payload, null, 2);
}

function toJsonMessage(message: ChatMessage, includeReasoning: boolean): JsonMessage {
  return {
    id: message.id,
    seq: message.seq,
    role: message.role,
    content: message.content,
    ...(includeReasoning ? { thinking: message.thinking } : {}),
    status: message.status,
    ...(message.error ? { error: toJsonError(message.error) } : {}),
    ...(message.stats ? { stats: message.stats } : {}),
    createdAt: message.createdAt,
  };
}

/** Drops `detail`, which the type itself documents as "Logged only, never rendered." */
function toJsonError(error: AppError): JsonError {
  return {
    code: error.code,
    message: error.message,
    ...(error.action ? { action: error.action } : {}),
  };
}

/* ---------------------------------------------------------------------- */
/* Filenames                                                                */
/* ---------------------------------------------------------------------- */

/**
 * Total byte budget for the whole filename (base + extension), well under
 * the 255-byte-per-component limit shared by NTFS, APFS and ext4. Measured
 * in UTF-8 bytes, not characters: a title made of 3-4-byte-per-character
 * emoji or CJK text can blow past 255 bytes at far fewer than 255
 * *characters*, so a plain character cap would not actually be safe.
 */
const MAX_FILENAME_BYTES = 200;

const DEFAULT_FILENAME_BASE = 'conversation';

/**
 * Windows device names, reserved as a filename regardless of extension or
 * case — `NUL.txt` is just as invalid as `NUL`, because the OS matches on
 * everything before the *first* dot, not the whole name.
 */
const RESERVED_DEVICE_NAMES = new Set([
  'CON',
  'PRN',
  'AUX',
  'NUL',
  'COM1',
  'COM2',
  'COM3',
  'COM4',
  'COM5',
  'COM6',
  'COM7',
  'COM8',
  'COM9',
  'LPT1',
  'LPT2',
  'LPT3',
  'LPT4',
  'LPT5',
  'LPT6',
  'LPT7',
  'LPT8',
  'LPT9',
]);

export function suggestFilename(title: string, format: ExportFormat): string {
  const extension = format === 'markdown' ? 'md' : 'json';
  return `${sanitizeFilenameBase(title, extension)}.${extension}`;
}

/** Drops C0 controls (0x00-0x1F) and DEL (0x7F); keeps every other code point untouched. */
function stripControlCharacters(text: string): string {
  let result = '';
  for (const char of text) {
    const codePoint = char.codePointAt(0) ?? 0;
    const isC0Control = codePoint <= 0x1f;
    const isDelete = codePoint === 0x7f;
    if (!isC0Control && !isDelete) result += char;
  }
  return result;
}

function sanitizeFilenameBase(title: string, extension: string): string {
  let base = title.normalize('NFC');

  // Control characters (including NUL) have no filename meaning and some are
  // rejected outright by real filesystems. Filtered by code point rather
  // than a \u-escape regex class, which iterates one Unicode code point at a
  // time (via for...of) and so never splits a surrogate pair either.
  base = stripControlCharacters(base);

  // `/` and `\` are the actual path-traversal vector — they are what turns
  // one filename component into a path. The rest are simply illegal on
  // Windows. All replaced with a space rather than dropped, so words do not
  // get jammed together (e.g. "Q&A: notes/todo" doesn't become "notestodo").
  base = base.replace(/[/\\:*?"<>|]/g, ' ');
  base = base.replace(/\s+/g, ' ').trim();

  // A leading dot (or run of them, e.g. what's left of "../../x" once the
  // slashes that gave it meaning are gone) makes a hidden file on POSIX; a
  // trailing dot or space is silently stripped by Windows and would make the
  // saved name diverge from what the user was shown.
  base = base.replace(/^[.\s]+/, '').replace(/[.\s]+$/, '');
  if (base.length === 0) base = DEFAULT_FILENAME_BASE;

  base = avoidDuplicateExtension(base, extension);
  if (base.length === 0) base = DEFAULT_FILENAME_BASE;

  base = avoidReservedDeviceName(base);
  base = truncateToByteBudget(base, extension);
  base = base.replace(/[.\s]+$/, '');

  return base.length > 0 ? base : DEFAULT_FILENAME_BASE;
}

/** "Notes.md" exported as Markdown should stay "Notes", not become "Notes.md.md". */
function avoidDuplicateExtension(base: string, extension: string): string {
  const suffix = `.${extension}`;
  return base.toLowerCase().endsWith(suffix) ? base.slice(0, base.length - suffix.length) : base;
}

/** The reserved check is on the segment before the first dot, per Windows' own rule. */
function avoidReservedDeviceName(base: string): string {
  const dotIndex = base.indexOf('.');
  const stem = dotIndex === -1 ? base : base.slice(0, dotIndex);
  if (!RESERVED_DEVICE_NAMES.has(stem.toUpperCase())) return base;

  const rest = dotIndex === -1 ? '' : base.slice(dotIndex);
  return `${stem}-export${rest}`;
}

function truncateToByteBudget(base: string, extension: string): string {
  const encoder = new TextEncoder();
  const budget = MAX_FILENAME_BYTES - encoder.encode(`.${extension}`).length;

  if (encoder.encode(base).length <= budget) return base;

  // Binary search over code points, never UTF-16 units, so an astral
  // character (e.g. an emoji) is never split inside its surrogate pair.
  const codePoints = [...base];
  let low = 0;
  let high = codePoints.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    const candidate = codePoints.slice(0, mid).join('');
    if (encoder.encode(candidate).length <= budget) low = mid;
    else high = mid - 1;
  }
  return codePoints.slice(0, low).join('');
}
