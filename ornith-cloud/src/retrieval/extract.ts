import { Readability } from '@mozilla/readability';
import { parseHTML } from 'linkedom';

/**
 * Readable-content extraction.
 *
 * linkedom rather than jsdom deliberately: it does not implement a script
 * runtime, so page JavaScript is never executed. That is a security property,
 * not just a performance one.
 */

export interface ExtractedContent {
  title: string;
  text: string;
  excerpt: string;
  truncated: boolean;
  /** 'readability' when the article parser succeeded, else the fallback used. */
  method: 'readability' | 'plaintext';
}

/** Strips tags without executing anything, for pages Readability rejects. */
export function stripToText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<svg[\s\S]*?<\/svg>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

export function extractContent(
  html: string,
  url: string,
  maxChars: number,
): ExtractedContent {
  let title = '';
  let text = '';
  let method: ExtractedContent['method'] = 'plaintext';

  try {
    const { document } = parseHTML(html);
    title = document.querySelector('title')?.textContent?.trim() ?? '';

    // Readability mutates the document, so it runs on the parsed copy only.
    // linkedom's Document is structurally compatible but not the DOM lib type,
    // which this server tsconfig deliberately does not include.
    type ReadabilityDoc = ConstructorParameters<typeof Readability>[0];
    const article = new Readability(document as unknown as ReadabilityDoc).parse();
    if (article?.textContent && article.textContent.trim().length > 200) {
      title = (article.title ?? title).trim();
      text = article.textContent.replace(/\s+/g, ' ').trim();
      method = 'readability';
    }
  } catch {
    // Fall through to the plain-text path; a malformed page is not fatal.
  }

  if (!text) {
    text = stripToText(html);
    method = 'plaintext';
  }

  const truncated = text.length > maxChars;
  if (truncated) text = text.slice(0, maxChars);

  if (!title) {
    try {
      title = new URL(url).hostname;
    } catch {
      title = url;
    }
  }

  return {
    title,
    text,
    excerpt: text.slice(0, 240),
    truncated,
    method,
  };
}
