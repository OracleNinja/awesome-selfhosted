/**
 * Turns a Markdown answer into something worth hearing.
 *
 * Reading a fenced code block aloud character by character is useless, and URLs
 * are worse. This strips what does not survive speech and keeps the prose.
 *
 * Pure: no I/O, fully unit-tested.
 */

/** Hard cap so one enormous answer cannot occupy the speech engine for minutes. */
export const MAX_SPEAKABLE_CHARS = 4000;

export function speakableText(markdown: string, maxChars = MAX_SPEAKABLE_CHARS): string {
  let text = markdown;

  // Fenced code: announce it rather than reciting it.
  text = text.replace(/```[\w+-]*\n[\s\S]*?```/g, ' (code block) ');
  // An unterminated fence, which happens when a stream is stopped mid-block.
  text = text.replace(/```[\w+-]*\n[\s\S]*$/g, ' (code block) ');

  // Inline code keeps its content; the backticks are noise.
  text = text.replace(/`([^`]+)`/g, '$1');

  // Images: read the alt text if there is any, otherwise drop them.
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
  // Links: read the label, never the URL.
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  // Bare URLs.
  text = text.replace(/\bhttps?:\/\/\S+/g, ' link ');

  // Tables do not linearise into speech usefully.
  text = text.replace(/^\s*\|.*\|\s*$/gm, ' (table) ');
  text = text.replace(/(\s*\(table\)\s*){2,}/g, ' (table) ');

  // Headings, emphasis, blockquotes, list bullets, rules.
  text = text.replace(/^#{1,6}\s+/gm, '');
  text = text.replace(/^\s{0,3}>\s?/gm, '');
  text = text.replace(/^\s*[-*+]\s+/gm, '');
  text = text.replace(/^\s*([-*_]\s*){3,}$/gm, ' ');
  text = text.replace(/(\*\*|__)(.*?)\1/g, '$2');
  text = text.replace(/(\*|_)(.*?)\1/g, '$2');
  text = text.replace(/~~(.*?)~~/g, '$2');

  // Collapse whitespace; keep paragraph breaks as sentence pauses.
  text = text.replace(/\r/g, '');
  text = text.replace(/[ \t]+/g, ' ');
  text = text.replace(/\n{2,}/g, '. ');
  text = text.replace(/\n/g, ' ');
  text = text.replace(/\s*\.\s*\./g, '.');
  // A colon or comma immediately followed by an inserted full stop reads badly.
  text = text.replace(/([:,;])\s*\./g, '$1');
  text = text.replace(/\s+/g, ' ').trim();
  // A leading separator left behind when the answer opened with a code block.
  text = text.replace(/^[.\s]+/, '');

  if (text.length <= maxChars) return text;

  // Truncate on a sentence boundary where possible, so it does not cut mid-word.
  const clipped = text.slice(0, maxChars);
  const lastStop = Math.max(clipped.lastIndexOf('. '), clipped.lastIndexOf('! '), clipped.lastIndexOf('? '));
  return lastStop > maxChars * 0.5 ? clipped.slice(0, lastStop + 1) : clipped.trimEnd();
}
