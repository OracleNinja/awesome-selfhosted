import { Children, isValidElement, memo } from 'react';
import type { ReactElement, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import CodeBlock from './CodeBlock';

/**
 * rehype-highlight replaces a code block's text with nested <span> elements, so
 * the original source has to be reassembled by walking the React tree.
 */
function nodeToText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeToText).join('');
  if (isValidElement(node)) {
    const props = node.props as { children?: ReactNode };
    return nodeToText(props.children);
  }
  return '';
}

function languageOf(element: ReactElement): string | undefined {
  const className = (element.props as { className?: string }).className ?? '';
  const match = /language-([\w+-]+)/.exec(className);
  return match?.[1];
}

/**
 * Overriding `pre` rather than `code` keeps inline code untouched — only fenced
 * blocks get the header and copy button.
 */
function Pre({ children }: { children?: ReactNode }) {
  const codeElement = Children.toArray(children).find(
    (child): child is ReactElement => isValidElement(child) && child.type === 'code',
  );

  if (!codeElement) return <pre className="code-block-body">{children}</pre>;

  const source = nodeToText(codeElement).replace(/\n$/, '');
  return (
    <CodeBlock code={source} language={languageOf(codeElement)}>
      {codeElement}
    </CodeBlock>
  );
}

function Anchor({ href, children }: { href?: string; children?: ReactNode }) {
  // Links open in the default browser via the main process window-open handler.
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
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{ pre: Pre, a: Anchor }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

// Streaming re-renders the whole list on every token; memoising keeps finished
// messages from re-parsing their Markdown each time.
export default memo(Markdown);
