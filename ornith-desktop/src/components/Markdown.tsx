import { Children, isValidElement, memo } from 'react';
import type { ReactElement, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CodeBlock from './CodeBlock';
import { rehypeHighlightSubset } from '../lib/rehypeHighlightSubset';

/**
 * rehype-highlight replaces a code block's text with nested spans, so the
 * original source has to be reassembled by walking the React tree.
 */
function nodeToText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeToText).join('');
  if (isValidElement(node)) {
    return nodeToText((node.props as { children?: ReactNode }).children);
  }
  return '';
}

function languageOf(element: ReactElement): string | undefined {
  const className = (element.props as { className?: string }).className ?? '';
  return /language-([\w+-]+)/.exec(className)?.[1];
}

/** Overriding `pre` keeps inline code untouched; only fenced blocks get chrome. */
function Pre({ children }: { children?: ReactNode }) {
  const codeElement = Children.toArray(children).find(
    (child): child is ReactElement => isValidElement(child) && child.type === 'code',
  );
  if (!codeElement) return <pre className="code-block-body">{children}</pre>;

  return (
    <CodeBlock code={nodeToText(codeElement).replace(/\n$/, '')} language={languageOf(codeElement)}>
      {codeElement}
    </CodeBlock>
  );
}

const SAFE_SCHEMES = /^(https?:|mailto:)/i;

function Anchor({ href, children }: { href?: string; children?: ReactNode }) {
  // Model output is untrusted: a file:// or javascript: link must not be clickable.
  if (!href || !SAFE_SCHEMES.test(href)) return <span>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

function Markdown({ content }: { content: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // No rehype-raw, deliberately: raw HTML from model output would be a
        // script-injection vector inside a privileged desktop app.
        rehypePlugins={[rehypeHighlightSubset]}
        components={{ pre: Pre, a: Anchor }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

// Completed messages must not re-parse while a later message streams.
export default memo(Markdown);
