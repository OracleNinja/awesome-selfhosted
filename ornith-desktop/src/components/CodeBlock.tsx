import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { bridge } from '../lib/bridge';

interface Props {
  /** The raw source, used for the copy button. */
  code: string;
  language?: string;
  /** The syntax-highlighted markup produced by rehype-highlight. */
  children: ReactNode;
}

export default function CodeBlock({ code, language, children }: Props) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = useCallback(() => {
    bridge.copyText(code);
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 1600);
  }, [code]);

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-lang">{language || 'text'}</span>
        <button
          className="copy-button"
          onClick={handleCopy}
          aria-label={copied ? 'Copied' : 'Copy code'}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="code-block-body">{children}</pre>
    </div>
  );
}
