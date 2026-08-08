import type { OllamaStatus } from '../../shared/types';

interface Props {
  status: OllamaStatus | null;
  onRetry: () => void;
}

export default function StatusIndicator({ status, onRetry }: Props) {
  if (status === null) {
    return (
      <div className="status-line" data-testid="status" data-state="connecting">
        <span className="status-dot" aria-hidden="true" />
        <span>Connecting…</span>
      </div>
    );
  }

  // Three states, because "connected but the model is missing" is a distinct
  // problem from "nothing is listening" and needs a different fix.
  const state = !status.connected ? 'disconnected' : status.activeModelInstalled ? 'ready' : 'model-missing';

  return (
    <div className="status-line" data-testid="status" data-state={state}>
      <span className={`status-dot is-${state}`} aria-hidden="true" />

      {state === 'ready' ? (
        <span className="status-text" title={`${status.host} · ${status.activeModel}`}>
          {status.activeModel}
        </span>
      ) : state === 'model-missing' ? (
        <span className="status-text status-warning">
          {status.activeModel} not installed
        </span>
      ) : (
        <>
          <span className="status-text status-error" title={status.error?.detail}>
            {status.error?.message ?? 'Ollama unreachable'}
          </span>
          <button type="button" className="retry-button" onClick={onRetry} data-testid="retry">
            Retry
          </button>
        </>
      )}
    </div>
  );
}
