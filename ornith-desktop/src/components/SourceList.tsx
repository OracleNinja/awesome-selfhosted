import type { AnsweredSource } from '../../shared/types';

interface Props {
  sources: AnsweredSource[];
}

/**
 * Sources the online backend actually consulted.
 *
 * Rendered from what the gateway reported, never from anything the model wrote,
 * so a fabricated citation cannot appear here.
 */
export default function SourceList({ sources }: Props) {
  if (sources.length === 0) return null;

  return (
    <div className="source-list" data-testid="source-list">
      <div className="source-list-label">Sources</div>
      <ol>
        {sources.map((source, index) => (
          <li key={`${source.url}-${index}`}>
            <a href={source.url} target="_blank" rel="noreferrer noopener" title={source.url}>
              {source.title}
            </a>
            <span className="source-domain">
              {source.domain}
              {source.cached ? ' · cached' : ''}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
