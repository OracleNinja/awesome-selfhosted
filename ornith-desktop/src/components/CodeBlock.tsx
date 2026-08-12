import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { bridge } from '../lib/bridge';

interface Props {
  code: string;
  language?: string;
  /** Highlighted markup produced by rehype-highlight. */
  children: ReactNode;
}

export default function CodeBlock({ code, language, children }: Props) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const handleCopy = useCallback(() => {
    void bridge.copyText(code);
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 1600);
  }, [code]);

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-lang">{language || 'text'}</span>
        <button
          type="button"
          className="copy-button"
          onClick={handleCopy}
          data-testid="copy-code"
          aria-label={copied ? 'Copied' : 'Copy code'}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="code-block-body">{children}</pre>
    </div>
  );
}
